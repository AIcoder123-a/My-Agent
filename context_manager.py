import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from agents import (
    Agent,
    Runner,
)

from config import model


# ============================================================
# 路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent

DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "conversation_history.db"
)


# ============================================================
# Context 策略
# ============================================================

# 阈值与体积估算统一收敛到 token_budget，
# 不再分散在 context_manager / memory 两处。
#
# 关键变化：除了条目数，现在还有一道 token 体积闸。
# 一条 Tool Output 实测可达 3.2 万字符（约 1 万 token），
# 只数条目完全挡不住撑爆窗口。
from token_budget import (
    CONTEXT_ITEM_LIMIT,
    MAX_SOURCE_CHARS,
    MAX_SUMMARY_CHARS,
    MIN_NEW_SUMMARY_ITEMS,
    RECENT_CONTEXT_ITEMS,
    RECENT_CONTEXT_TOKENS,
    SUMMARY_TRIGGER_ITEMS,
    SUMMARY_TRIGGER_TOKENS,
    estimate_items_tokens,
    token_cutoff,
)


# ============================================================
# Summary Agent
# ============================================================

summary_agent = Agent(
    name="上下文压缩器",

    instructions="""
你是 AI Agent 的上下文压缩器。

你的唯一任务是把旧对话压缩成高信息密度的长期上下文摘要。

必须保留真正影响后续任务的信息，例如：

- 用户当前目标
- 已完成工作
- 当前进度
- 用户明确要求和约束
- 已做出的关键技术决策
- 文件路径、项目结构、环境、版本
- 已验证成功的功能
- 已失败且不应重复的方案
- 当前错误与未解决问题
- 重要工具、参数、配置
- 下一步任务
- 用户明确要求长期记住的偏好

不要保留：

- 寒暄
- 重复内容
- 无价值的中间解释
- 已被后续结论取代的错误猜测

如果已经给出了“旧摘要”，请在旧摘要基础上更新，
不要简单把新内容附加到末尾。

输出要求：

1. 中文。
2. 使用清晰 Markdown。
3. 信息密度高。
4. 不要回答用户。
5. 不要加入摘要材料中不存在的新事实。
6. 控制在约 3000～5000 中文字符以内。
""".strip(),

    model=model,

    tools=[],
)


# ============================================================
# SQLite
# ============================================================

SUMMARY_TABLE = "app_context_summaries"


def _connect():

    conn = sqlite3.connect(
        str(DB_PATH)
    )

    conn.row_factory = sqlite3.Row

    return conn


def _init_schema():

    with _connect() as conn:

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS
            {SUMMARY_TABLE}
            (
                session_id TEXT PRIMARY KEY,

                summary TEXT NOT NULL
                    DEFAULT '',

                summarized_items INTEGER NOT NULL
                    DEFAULT 0,

                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()


# ============================================================
# Summary Record
# ============================================================

def _summary_record(
    session_id: str,
) -> dict:

    _init_schema()

    with _connect() as conn:

        row = conn.execute(
            f"""
            SELECT
                summary,
                summarized_items,
                updated_at

            FROM
                {SUMMARY_TABLE}

            WHERE
                session_id = ?
            """,
            (
                session_id,
            ),
        ).fetchone()

    if not row:

        return {
            "summary": "",
            "summarized_items": 0,
            "updated_at": "",
        }

    return {
        "summary":
            row["summary"] or "",

        "summarized_items":
            int(
                row["summarized_items"]
                or 0
            ),

        "updated_at":
            row["updated_at"] or "",
    }


def get_summary(
    session_id: str,
) -> str:

    return _summary_record(
        session_id
    )["summary"]


def save_summary(
    session_id: str,
    summary: str,
    summarized_items: int,
):

    _init_schema()

    summary = (
        summary or ""
    ).strip()

    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[
            :MAX_SUMMARY_CHARS
        ]

    with _connect() as conn:

        conn.execute(
            f"""
            INSERT INTO
                {SUMMARY_TABLE}
            (
                session_id,
                summary,
                summarized_items,
                updated_at
            )

            VALUES
            (
                ?, ?, ?,
                CURRENT_TIMESTAMP
            )

            ON CONFLICT(session_id)
            DO UPDATE SET

                summary =
                    excluded.summary,

                summarized_items =
                    excluded.summarized_items,

                updated_at =
                    CURRENT_TIMESTAMP
            """,
            (
                session_id,
                summary,
                int(
                    summarized_items
                ),
            ),
        )

        conn.commit()


def delete_summary(
    session_id: str,
):

    _init_schema()

    with _connect() as conn:

        conn.execute(
            f"""
            DELETE FROM
                {SUMMARY_TABLE}

            WHERE
                session_id = ?
            """,
            (
                session_id,
            ),
        )

        conn.commit()


# ============================================================
# 读取完整 Session
# ============================================================

def _load_all_items(
    session_id: str,
) -> list[dict]:
    """
    直接读取 SQLite 中完整历史。

    这里不能直接 session.get_items()，
    因为项目中的 SQLiteSession 默认设置了历史读取 limit。

    Context 压缩需要知道完整历史中的绝对 Item 位置，
    因此这里直接读取 agent_messages。
    """

    items = []

    with _connect() as conn:

        try:

            rows = conn.execute(
                """
                SELECT message_data
                FROM agent_messages

                WHERE session_id = ?

                ORDER BY id ASC
                """,
                (
                    session_id,
                ),
            ).fetchall()

        except sqlite3.Error:

            return []

    for row in rows:

        try:

            value = json.loads(
                row["message_data"]
            )

        except Exception:
            continue

        if isinstance(
            value,
            dict,
        ):
            items.append(
                value
            )

    return items


# ============================================================
# Tool Call 边界安全
# ============================================================

TOOL_CALL_TYPES = {
    "function_call",
    "tool_call",
}

TOOL_OUTPUT_TYPES = {
    "function_call_output",
    "tool_output",
}


def _item_call_id(
    item: dict,
) -> str:
    """
    从不同 Tool Item 形式中尽量提取稳定的 call id。
    """

    value = (
        item.get("call_id")
        or item.get("tool_call_id")
    )

    if value is None:
        return ""

    return str(value)


def _is_tool_call(
    item: dict,
) -> bool:

    return (
        item.get("type")
        in TOOL_CALL_TYPES
    )


def _is_tool_output(
    item: dict,
) -> bool:

    return (
        item.get("type")
        in TOOL_OUTPUT_TYPES
    )


def _find_matching_call_index(
    items: list[dict],
    output_index: int,
) -> int | None:
    """
    为一个 Tool Output 向前寻找对应的 Tool Call。
    """

    if (
        output_index < 0
        or output_index >= len(items)
    ):
        return None

    output_item = items[
        output_index
    ]

    if not _is_tool_output(
        output_item
    ):
        return None

    call_id = _item_call_id(
        output_item
    )

    if not call_id:
        return None

    for index in range(
        output_index - 1,
        -1,
        -1,
    ):

        item = items[index]

        if not _is_tool_call(
            item
        ):
            continue

        if (
            _item_call_id(item)
            == call_id
        ):
            return index

    return None


def _find_safe_boundary(
    items: list[dict],
    desired_boundary: int,
) -> int:
    """
    返回一个 Tool Call 安全的历史切分点。

    boundary 表示：

        items[:boundary]
            -> 可进入长期摘要

        items[boundary:]
            -> 保留为原始模型上下文

    如果 boundary 之后存在 Tool Output，
    但对应 Tool Call 位于 boundary 之前，
    就把 boundary 向前移动到该 Tool Call。

    这样可以避免模型最终收到：

        tool output
        但前面没有对应 tool call

    从而避免 Chat Completions 400：
        "Messages with role 'tool' must be a response
         to a preceding message with 'tool_calls'"
    """

    boundary = max(
        0,
        min(
            int(desired_boundary),
            len(items),
        ),
    )

    while True:

        changed = False

        for index in range(
            boundary,
            len(items),
        ):

            item = items[index]

            if not _is_tool_output(
                item
            ):
                continue

            call_index = (
                _find_matching_call_index(
                    items,
                    index,
                )
            )

            if (
                call_index is not None
                and call_index < boundary
            ):

                boundary = (
                    call_index
                )

                changed = True
                break

        if not changed:
            break

    return boundary


def _remove_orphan_tool_outputs(
    items: list[dict],
) -> list[dict]:
    """
    最后一层防御：

    如果历史本身已经损坏，某个 Tool Output
    在当前原始上下文中完全找不到对应 Tool Call，
    就不把这个孤立 Output 发送给模型。

    正常情况下，_find_safe_boundary() 已经足以保证配对，
    这个函数主要用于兼容旧数据或异常中断留下的脏历史。
    """

    result = []
    seen_call_ids = set()

    for item in items:

        if _is_tool_call(
            item
        ):

            call_id = _item_call_id(
                item
            )

            if call_id:
                seen_call_ids.add(
                    call_id
                )

            result.append(
                item
            )
            continue

        if _is_tool_output(
            item
        ):

            call_id = _item_call_id(
                item
            )

            if (
                call_id
                and call_id
                not in seen_call_ids
            ):
                # 孤立 Tool Output：
                # 不发送给模型，避免协议级 400。
                continue

        result.append(
            item
        )

    return result


def _remove_unanswered_tool_calls(
    items: list[dict],
) -> list[dict]:
    """
    删除“没有结果”的 Tool Call。

    场景：
    HITL 审批被用户放弃（关闭窗口 / 从不点击），
    或进程在 Tool 执行前中断。

    此时 SQLite 里会留下：

        assistant: function_call(call_123)
        （没有对应的 function_call_output）

    OpenAI-compatible API 会直接 400：

        An assistant message with 'tool_calls'
        must be followed by tool messages
        responding to each tool_call_id

    因此这类悬空 Tool Call 必须从
    发给模型的历史中剔除。

    注意：
    只删除悬空 Tool Call 本身；
    完整原始历史仍然保留在 SQLite 中。
    """

    answered = set()

    for item in items:

        if _is_tool_output(
            item
        ):

            call_id = (
                _item_call_id(
                    item
                )
            )

            if call_id:

                answered.add(
                    call_id
                )

    result = []

    for item in items:

        if _is_tool_call(
            item
        ):

            call_id = (
                _item_call_id(
                    item
                )
            )

            if (
                call_id
                and call_id
                not in answered
            ):

                continue

        result.append(
            item
        )

    return result


# ============================================================
# Item → Summary Text
# ============================================================

def _extract_content_text(
    content: Any,
) -> str:

    if content is None:
        return ""

    if isinstance(
        content,
        str,
    ):
        return content

    if not isinstance(
        content,
        list,
    ):
        return str(content)

    parts = []

    for part in content:

        if isinstance(
            part,
            str,
        ):

            parts.append(part)
            continue

        if not isinstance(
            part,
            dict,
        ):
            continue

        text = (
            part.get("text")
            or part.get("content")
            or ""
        )

        if text:

            parts.append(
                str(text)
            )

    return "\n".join(
        parts
    )


def _item_to_text(
    item: dict,
) -> str:

    item_type = (
        item.get("type")
        or ""
    )

    role = (
        item.get("role")
        or ""
    )

    # -----------------------------
    # 普通用户/助手消息
    # -----------------------------

    if role in {
        "user",
        "assistant",
        "system",
        "developer",
    }:

        content = _extract_content_text(
            item.get(
                "content"
            )
        )

        if not content:
            return ""

        return (
            f"[{role}]\n"
            f"{content}"
        )

    # -----------------------------
    # Tool Call
    # -----------------------------

    if item_type in TOOL_CALL_TYPES:

        name = (
            item.get("name")
            or "unknown"
        )

        arguments = (
            item.get("arguments")
            or item.get("input")
            or ""
        )

        return (
            f"[tool_call: {name}]\n"
            f"{arguments}"
        )

    # -----------------------------
    # Tool Output
    # -----------------------------

    if item_type in TOOL_OUTPUT_TYPES:

        output = (
            item.get("output")
            or ""
        )

        output = str(
            output
        )

        # Tool Output 不允许无限增长。
        if len(output) > 3000:

            output = (
                output[:3000]
                + "\n...[截断]"
            )

        return (
            "[tool_output]\n"
            + output
        )

    return ""


def _items_to_source(
    items: list[dict],
) -> str:

    blocks = []

    total_chars = 0

    for item in items:

        text = _item_to_text(
            item
        ).strip()

        if not text:
            continue

        block = (
            text
            + "\n\n"
        )

        remaining = (
            MAX_SOURCE_CHARS
            - total_chars
        )

        if remaining <= 0:
            break

        if len(block) > remaining:

            blocks.append(
                block[:remaining]
            )

            break

        blocks.append(
            block
        )

        total_chars += len(
            block
        )

    return "".join(
        blocks
    ).strip()


# ============================================================
# Compaction Plan
# ============================================================

def get_compaction_plan(
    session_id: str,
) -> dict:

    items = _load_all_items(
        session_id
    )

    total_items = len(
        items
    )

    record = _summary_record(
        session_id
    )

    summarized_items = max(
        0,
        min(
            int(
                record[
                    "summarized_items"
                ]
            ),
            total_items,
        ),
    )

    # 48 是目标保留量，不是绝对硬切点。
    desired_cutoff = max(
        0,
        total_items
        - RECENT_CONTEXT_ITEMS,
    )

    # 第二道闸：体积。
    #
    # 48 条小消息很安全，
    # 但 48 条 3 万字符的 Tool Output 就是几十万 token。
    # 这里从末尾按 token 预算再切一次，
    # 两个切点取更靠后的那个（保留更少 = 更保守）。

    cutoff_by_tokens = (
        token_cutoff(
            items,
            RECENT_CONTEXT_TOKENS,
        )
    )

    desired_cutoff = max(
        desired_cutoff,
        cutoff_by_tokens,
    )

    # 如果 desired_cutoff 落在
    # Tool Call / Tool Output 中间，
    # 自动向前移动到安全位置。
    safe_cutoff = (
        _find_safe_boundary(
            items,
            desired_cutoff,
        )
    )

    # 永远不要把已经保存的摘要游标倒退。
    #
    # 旧摘要即使是在旧版本的“不安全边界”上生成，
    # Callback 会单独通过 safe raw start
    # 向前重叠保留必要 Tool Call。
    target_summarized_items = max(
        summarized_items,
        safe_cutoff,
    )

    new_summary_items = max(
        0,
        target_summarized_items
        - summarized_items,
    )

    estimated_tokens = (
        estimate_items_tokens(
            items
        )
    )

    trigger_by_items = (
        total_items
        > SUMMARY_TRIGGER_ITEMS
        and new_summary_items
        >= MIN_NEW_SUMMARY_ITEMS
    )

    # 体积越线时不再等「新条目攒够」：
    # 撑爆窗口的代价远大于多调一次摘要模型。
    trigger_by_tokens = (
        estimated_tokens
        > SUMMARY_TRIGGER_TOKENS
    )

    needed = bool(
        trigger_by_items
        or trigger_by_tokens
    )

    return {
        "needed":
            needed,

        "total_items":
            total_items,

        "summarized_items":
            summarized_items,

        "desired_summarized_items":
            desired_cutoff,

        "target_summarized_items":
            target_summarized_items,

        "safe_cutoff":
            safe_cutoff,

        "new_summary_items":
            new_summary_items,

        "has_summary":
            bool(
                record["summary"]
            ),
    }


# ============================================================
# 自动摘要
# ============================================================

async def maybe_compact_context(
    session_id: str,
) -> dict:
    """
    如果达到阈值，生成/更新长期摘要。

    这个 Runner 不绑定 Session，
    所以摘要任务不会写入用户聊天历史。
    """

    plan = get_compaction_plan(
        session_id
    )

    if not plan["needed"]:

        return {
            **plan,
            "compacted": False,
            "error": "",
        }

    items = _load_all_items(
        session_id
    )

    record = _summary_record(
        session_id
    )

    old_summary = (
        record["summary"]
        or ""
    )

    # 旧摘要游标也可能来自旧版 Context，
    # 因而恰好落在 Tool Call / Output 中间。
    #
    # 这里允许向前少量重叠，让本次摘要源文本
    # 尽量包含完整工具调用配对。
    desired_start = max(
        0,
        min(
            int(
                plan[
                    "summarized_items"
                ]
            ),
            len(items),
        ),
    )

    start = _find_safe_boundary(
        items,
        desired_start,
    )

    end = max(
        start,
        min(
            int(
                plan[
                    "target_summarized_items"
                ]
            ),
            len(items),
        ),
    )

    new_old_items = items[
        start:end
    ]

    source = _items_to_source(
        new_old_items
    )

    if not source:

        return {
            **plan,
            "compacted": False,
            "error":
                "没有可用于摘要的有效文本。",
        }

    prompt = f"""
请更新这个会话的长期上下文摘要。

【已有摘要】
{old_summary if old_summary else "暂无。"}

【本次新增需要压缩的旧历史】
{source}

请输出更新后的完整摘要。
不要解释你的工作过程。
""".strip()

    try:

        result = await Runner.run(
            summary_agent,
            prompt,
            max_turns=1,
        )

        summary = str(
            result.final_output
            or ""
        ).strip()

        if not summary:

            raise RuntimeError(
                "摘要模型返回了空内容。"
            )

        save_summary(
            session_id,
            summary,
            summarized_items=end,
        )

        return {
            **plan,
            "compacted": True,
            "summary_length":
                len(summary),

            "compaction_source_start":
                start,

            "compaction_source_end":
                end,

            "error": "",
        }

    except Exception as error:

        # 摘要失败不能影响用户正常聊天。
        return {
            **plan,
            "compacted": False,
            "error": (
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }


# ============================================================
# Session Input Callback
# ============================================================

def build_session_input_callback(
    session_id: str,
) -> Callable:
    """
    模型最终看到：

        长期摘要（如果存在）
        +
        尚未进入摘要的原始历史
        （至少保留最近约 48 项，
         Tool Call 边界会自动向前扩展）
        +
        当前用户输入

    SQLite 仍保存全部原始历史。

    重要：
    不再机械使用 history[-48:]。

    原因：
    1. 固定切片可能把 function_call 和
       function_call_output 拆开，导致 400。
    2. 已有长期摘要后，在下一次增量摘要触发之前，
       “摘要游标之后”的全部原始 Item 都必须保留。
    """

    def merge_context(
        history: list[Any],
        new_input: list[Any],
    ) -> list[Any]:

        # 不依赖 callback 参数 history 的绝对位置。
        #
        # history 可能已经受 SessionSettings(limit=...)
        # 影响，只包含最近 N 项。
        # 我们需要 summarized_items 对应完整 SQLite 历史
        # 的绝对索引，因此直接读取完整 Session。
        all_items = _load_all_items(
            session_id
        )

        record = _summary_record(
            session_id
        )

        summary = (
            record["summary"]
            or ""
        )

        total_items = len(
            all_items
        )

        if summary:

            # 有摘要：
            # 从“已摘要游标”之后保留全部原始尾部。
            #
            # 如果旧游标本身恰好落在 Tool Output 上，
            # _find_safe_boundary 会向前移动，
            # 允许少量“摘要 + 原文”重叠，
            # 以换取协议完整性。
            desired_start = max(
                0,
                min(
                    int(
                        record[
                            "summarized_items"
                        ]
                    ),
                    total_items,
                ),
            )

        else:

            # 没有摘要：
            # 延续原设计，只保留最近约 48 项，
            # 但切点必须 Tool-safe。
            desired_start = max(
                0,
                total_items
                - RECENT_CONTEXT_ITEMS,
            )

        safe_start = _find_safe_boundary(
            all_items,
            desired_start,
        )

        raw_history = all_items[
            safe_start:
        ]

        # 防御旧数据或异常中断导致的真正孤立 Tool Output。
        raw_history = (
            _remove_orphan_tool_outputs(
                raw_history
            )
        )

        # 防御被放弃的 HITL 审批：
        # 悬空 Tool Call 会让 API 直接 400。
        raw_history = (
            _remove_unanswered_tool_calls(
                raw_history
            )
        )

        # ------------------------------------------------
        # 读路径体积硬闸。
        #
        # 上面的条目数与游标只决定「哪些应该保留」，
        # 但摘要可能尚未生成或持续失败（压缩触发在写路径，
        # 失败后不会重试到下一轮）——此时尾部原始历史
        # 条目数再合法，体积也可能已经爆窗。
        # 这里从末尾向前按体积预算兜底截断；
        # 切点若落在 Tool 配对中间，先推进到安全边界再清理。
        # ------------------------------------------------
        budget_boundary = token_cutoff(
            raw_history,
            RECENT_CONTEXT_TOKENS,
        )

        if budget_boundary > 0:

            safe_budget_start = (
                _find_safe_boundary(
                    raw_history,
                    budget_boundary,
                )
            )

            raw_history = raw_history[
                safe_budget_start:
            ]

            raw_history = (
                _remove_orphan_tool_outputs(
                    raw_history
                )
            )

            raw_history = (
                _remove_unanswered_tool_calls(
                    raw_history
                )
            )

        result = []

        if summary:

            result.append(
                {
                    "role": "system",
                    "content": (
                        "下面是当前会话较早历史的"
                        "压缩上下文摘要。"
                        "它只用于恢复长期背景，"
                        "不是用户当前的新请求。\n\n"
                        + summary
                    ),
                }
            )

        result.extend(
            raw_history
        )

        # 当前轮 new_input 必须始终保留。
        result.extend(
            new_input
        )

        return result

    return merge_context


# ============================================================
# Context Status
# ============================================================

def get_context_status(
    session_id: str,
) -> dict:

    items = _load_all_items(
        session_id
    )

    record = _summary_record(
        session_id
    )

    total_items = len(
        items
    )

    summary = (
        record["summary"]
        or ""
    )

    summarized_items = max(
        0,
        min(
            int(
                record[
                    "summarized_items"
                ]
            ),
            total_items,
        ),
    )

    if summary:

        desired_raw_start = (
            summarized_items
        )

    else:

        desired_raw_start = max(
            0,
            total_items
            - RECENT_CONTEXT_ITEMS,
        )

    safe_raw_start = (
        _find_safe_boundary(
            items,
            desired_raw_start,
        )
    )

    raw_context_items = (
        total_items
        - safe_raw_start
    )

    return {
        "session_id":
            session_id,

        "total_items":
            total_items,

        # 保留旧字段名，避免 GUI / 其他代码兼容性问题。
        # 现在它表示“实际将保留的原始历史数量”，
        # 可能因 Tool-safe 边界而大于 48。
        "recent_context_items":
            raw_context_items,

        "recent_context_limit":
            RECENT_CONTEXT_ITEMS,

        "summary_trigger":
            SUMMARY_TRIGGER_ITEMS,

        "has_summary":
            bool(summary),

        "summary_length":
            len(summary),

        "summarized_items":
            summarized_items,

        "summary_updated_at":
            record[
                "updated_at"
            ],

        # 新增调试字段。
        "safe_raw_start":
            safe_raw_start,

        "raw_context_items":
            raw_context_items,
    }


_init_schema()
