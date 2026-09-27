"""上下文体积估算与统一阈值。

为什么需要这个模块
==================

原先的上下文管理全程按「条目数」做决策：

    context_manager.RECENT_CONTEXT_ITEMS = 48
    context_manager.SUMMARY_TRIGGER_ITEMS = 80
    memory.CONTEXT_ITEM_LIMIT           = 80

但一条 Tool Output 实测可达 3.2 万字符（约 1 万 token）。
48 条这样的条目就是 48 万 token，
远超 128K 窗口 —— 还没到压缩阈值就已经撑爆了。
而三个常量分散在两个文件里且互不一致，改一处漏两处。

这里提供两件事：
1. 一个不依赖任何第三方、也不依赖项目其它模块体积估算函数，
   供上下文切分与压缩触发使用；
2. 所有阈值常量的唯一来源。

关于估算精度
============

不做真实分词（那需要 tiktoken 之类的依赖，
且各家模型的分词还不一样）。

按字符类别分别折算，两个方向都偏保守（宁可估多）：

    英文/ASCII 约 4 字符/token，按 3.5 折算；
    中文约 1.3~1.7 字符/token（DeepSeek 偏上限，
    OpenAI 偏下限），按 1.2 折算。

旧实现统一按「3 字符 ≈ 1 token」，自称“宁可估多”，
但对中文恰好是估少 —— 一段中文的真实 token 数
最高可达旧估算的 2.5 倍。对一个固定用中文回答的
Agent，估少意味着压缩永远来得太晚、窗口被撑爆。
"""
import json
import re


# ============================================================
# 估算
# ============================================================

# 中文类字符（汉字/假名/谚文/全角标点）的折算：
# 多少个字符算 1 个 token。取 1.2 —— 比各家真实分词
# 都更「费 token」，方向是提前压缩而不是事后爆窗。
CJK_CHARS_PER_TOKEN = 1.2

# 英文/数字/符号的折算。
OTHER_CHARS_PER_TOKEN = 3.5

# CJK 统一表意文字、部首、假名、谚文、全角形式，
# 以及扩展 A-F（ astral 平面）。
_CJK_RE = re.compile(
    "["
    "\u2e80-\u9fff"
    "\uf900-\ufaff"
    "\uff00-\uffef"
    "\U00020000-\U0002fa1f"
    "]"
)


def estimate_tokens(
    text,
) -> int:
    """
    粗略估算一段文本的 token 数。
    """

    if text is None:
        return 0

    raw = str(
        text
    )

    if not raw:
        return 0

    cjk = sum(
        1
        for _ in _CJK_RE.finditer(
            raw
        )
    )

    other = len(
        raw
    ) - cjk

    total = (
        cjk / CJK_CHARS_PER_TOKEN
        + other / OTHER_CHARS_PER_TOKEN
    )

    return max(
        1,
        int(
            total
        ),
    )


def estimate_item_tokens(
    item,
) -> int:
    """
    估算一条 Session Item 的 token 数。

    Item 是 dict，直接 json 序列化后估算，
    这样 arguments / content / output 各字段都能计入。
    """

    try:

        return estimate_tokens(
            json.dumps(
                item,
                ensure_ascii=(
                    False
                ),
                default=str,
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return estimate_tokens(
            str(
                item
            )
        )


def estimate_items_tokens(
    items,
) -> int:
    """
    估算一组 Item 的总 token 数。
    """

    return sum(
        estimate_item_tokens(
            item
        )
        for item in (
            items or []
        )
    )


def token_cutoff(
    items,
    budget: int,
) -> int:
    """
    从末尾向前累加，返回「保留总量不超过预算」的切分点。

    返回 boundary，含义与 context_manager 中的一致：

        items[:boundary]  -> 可进入长期摘要
        items[boundary:]  -> 保留为原始模型上下文

    保证 items[boundary:] 的估算总量 <= budget。
    单条就超预算时，仍至少保留最后一条，
    否则模型会完全看不到刚刚发生了什么。
    """

    items = list(
        items or []
    )

    if not items:
        return 0

    total = 0

    for index in range(
        len(items) - 1,
        -1,
        -1,
    ):

        total += estimate_item_tokens(
            items[index]
        )

        if (
            total
            > budget
        ):

            # 加上第 index 条就超了，
            # 因此从 index + 1 开始保留。
            return min(
                index + 1,
                len(items) - 1,
            )

    return 0


# ============================================================
# 统一阈值
# ============================================================

# SDK Session 每次读取给模型的最大条目数。
#
# 这是「读取上限」，不等于「保留量」：
# 读进来之后 context_manager 还会再做一次切分，
# 只把最近 RECENT_CONTEXT_ITEMS 条留在原始上下文里。
CONTEXT_ITEM_LIMIT = 80

# 希望主 Agent 至少保留的最近原始历史条目数。
RECENT_CONTEXT_ITEMS = 48

# 总历史超过这个条目数后开始考虑压缩。
SUMMARY_TRIGGER_ITEMS = 80

# 已经存在摘要后，至少再累积这么多可压缩条目
# 才重新生成一次增量摘要，避免每聊一句就调一次摘要模型。
MIN_NEW_SUMMARY_ITEMS = 16

# ------------------------------------------------------------
# 体积预算（第二道闸）
#
# 条目数够少 ≠ 体积够小。
# 下面两个预算与上面的条目阈值取「更保守」的一侧生效。
# ------------------------------------------------------------

# 保留给模型的近期原始历史预算。
# 留出足够空间给 system prompt、工具定义、
# 长期摘要与模型生成，避免撑爆 128K 窗口。
RECENT_CONTEXT_TOKENS = 30000

# 总历史估算超过这个量就触发压缩。
# 触发时不看「新增条目数够不够」——
# 体积已经越线时，省一次摘要调用远不如避免撑爆重要。
SUMMARY_TRIGGER_TOKENS = 45000

# 防止一次摘要 Prompt 过大。
MAX_SOURCE_CHARS = 32000

# 最多保留的摘要字符。
MAX_SUMMARY_CHARS = 6000
