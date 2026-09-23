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

中英混排场景取「3 字符 ≈ 1 token」：
    英文约 4 字符/token，
    中文约 1~1.5 字符/token。
3 是偏保守的折中 —— 宁可估多、提前压缩，
也不要估少、把窗口撑爆。
"""
import json


# ============================================================
# 估算
# ============================================================

# 中英混排的保守折算：多少个字符算 1 个 token
CHARS_PER_TOKEN = 3


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

    return max(
        1,
        int(
            len(
                raw
            )
            / CHARS_PER_TOKEN
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
