import sqlite3
import uuid
from pathlib import Path
from typing import Any

from agents import (
    SQLiteSession,
    SessionSettings,
)


# ============================================================
# 路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

DB_PATH = (
    DATA_DIR
    / "conversation_history.db"
)

CURRENT_SESSION_FILE = (
    DATA_DIR
    / "current_session.txt"
)


# ============================================================
# 上下文策略
# ============================================================

# 条目与体积阈值的唯一来源是 token_budget，
# 这里不再自己定义一份，避免两处漂移。
from token_budget import (
    CONTEXT_ITEM_LIMIT,
)


# ============================================================
# SQLite App Metadata
# ============================================================

APP_SESSION_TABLE = (
    "app_conversations"
)


def _connect():
    """
    创建应用层 SQLite 连接。
    """

    conn = sqlite3.connect(
        str(DB_PATH)
    )

    conn.row_factory = sqlite3.Row

    return conn


def _sdk_tables_exist(
    conn,
) -> bool:

    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'agent_sessions'
        """
    ).fetchone()

    return row is not None


def _init_app_schema():
    """
    初始化我们自己的 Session Metadata 表。

    Agents SDK 自己管理：
        agent_sessions
        agent_messages

    我们额外管理：
        app_conversations

    用于：
        会话名称
        会话列表
        UI 排序
    """

    with _connect() as conn:

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS
            {APP_SESSION_TABLE}
            (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL
                    DEFAULT '新对话',
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()

        # --------------------------------------------
        # 将以前已经存在的 SDK Session 补进元数据表
        # --------------------------------------------

        if _sdk_tables_exist(conn):

            rows = conn.execute(
                """
                SELECT
                    session_id,
                    created_at,
                    updated_at
                FROM agent_sessions
                """
            ).fetchall()

            for row in rows:

                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO
                    {APP_SESSION_TABLE}
                    (
                        session_id,
                        title,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row["session_id"],
                        "历史会话",
                        row["created_at"]
                        or "",
                        row["updated_at"]
                        or "",
                    ),
                )

            conn.commit()


# ============================================================
# Session Factory
# ============================================================

def _make_session(
    session_id: str,
) -> SQLiteSession:
    """
    创建一个指向指定 Session ID 的 SDK Session。

    CONTEXT_ITEM_LIMIT 只影响读取给模型的历史数量，
    不影响数据库保存完整历史。

    分层关系（不是重复，也不是笔误）：

        SQLiteSession.limit = CONTEXT_ITEM_LIMIT (80)
            └─ 从数据库读多少条给 Agent

        context_manager 的切分
            ├─ RECENT_CONTEXT_ITEMS (48)
            └─ RECENT_CONTEXT_TOKENS (30000)
                └─ 读进来之后再切一次，
                   只把这部分留在原始上下文里，
                   其余进入长期摘要

    SDK 的 limit 只支持条目数，不支持体积，
    所以体积那一道闸必须在 context_manager 里补。
    """

    session = SQLiteSession(
        session_id=session_id,
        db_path=DB_PATH,
        session_settings=SessionSettings(
            limit=CONTEXT_ITEM_LIMIT
        ),
    )

    return session


# ============================================================
# Session ID
# ============================================================

def _new_session_id() -> str:

    return (
        "v2_"
        + str(uuid.uuid4())
    )


def _read_current_session_id():
    """
    读取当前 Session ID。
    """

    if (
        not CURRENT_SESSION_FILE.exists()
    ):
        return None

    try:

        session_id = (
            CURRENT_SESSION_FILE
            .read_text(
                encoding="utf-8"
            )
            .strip()
        )

    except Exception:
        return None

    return (
        session_id
        if session_id
        else None
    )


def _write_current_session_id(
    session_id: str,
):

    CURRENT_SESSION_FILE.write_text(
        session_id,
        encoding="utf-8",
    )


# ============================================================
# Metadata
# ============================================================

def _ensure_metadata(
    session_id: str,
    title: str = "新对话",
):

    _init_app_schema()

    with _connect() as conn:

        conn.execute(
            f"""
            INSERT OR IGNORE INTO
            {APP_SESSION_TABLE}
            (
                session_id,
                title
            )
            VALUES (?, ?)
            """,
            (
                session_id,
                title,
            ),
        )

        conn.commit()


def touch_session(
    session_id: str,
):
    """
    更新会话最后活动时间。
    """

    _ensure_metadata(
        session_id
    )

    with _connect() as conn:

        conn.execute(
            f"""
            UPDATE
                {APP_SESSION_TABLE}
            SET
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE
                session_id = ?
            """,
            (
                session_id,
            ),
        )

        conn.commit()


# ============================================================
# 保留旧接口
# ============================================================

def load_or_create_session():
    """
    加载当前 Session。

    没有则自动创建。

    保留原有返回格式：
        session, session_id
    """

    session_id = (
        _read_current_session_id()
    )

    if not session_id:

        return create_new_session()

    session = _make_session(
        session_id
    )

    _ensure_metadata(
        session_id
    )

    return (
        session,
        session_id,
    )


def create_new_session():
    """
    创建新的 Conversation Session。

    保留原有返回格式：
        session, session_id
    """

    session_id = (
        _new_session_id()
    )

    session = _make_session(
        session_id
    )

    _ensure_metadata(
        session_id,
        title="新对话",
    )

    _write_current_session_id(
        session_id
    )

    return (
        session,
        session_id,
    )


# ============================================================
# 历史会话
# ============================================================

def list_sessions(
    limit: int = 100,
):
    """
    返回历史 Session。

    最新活动排在最前面。
    """

    _init_app_schema()

    current_session_id = (
        _read_current_session_id()
    )

    with _connect() as conn:

        rows = conn.execute(
            f"""
            SELECT
                c.session_id,
                c.title,
                c.created_at,
                c.updated_at,

                (
                    SELECT COUNT(*)
                    FROM agent_messages m
                    WHERE
                        m.session_id
                        = c.session_id
                )
                AS item_count

            FROM
                {APP_SESSION_TABLE} c

            ORDER BY
                c.updated_at DESC,

                -- updated_at 只精确到秒，同一秒内创建的多个会话
                -- 排序结果不稳定，列表会在每次刷新后跳来跳去。
                -- 补两级排序让它变成确定的。
                c.created_at DESC,
                c.session_id DESC

            LIMIT ?
            """,
            (
                limit,
            ),
        ).fetchall()

    result = []

    for row in rows:

        result.append(
            {
                "session_id":
                    row["session_id"],

                "title":
                    row["title"],

                "created_at":
                    row["created_at"],

                "updated_at":
                    row["updated_at"],

                "item_count":
                    row["item_count"],

                "current":
                    (
                        row["session_id"]
                        == current_session_id
                    ),
            }
        )

    return result


def session_exists(
    session_id: str,
) -> bool:

    _init_app_schema()

    with _connect() as conn:

        row = conn.execute(
            f"""
            SELECT 1
            FROM
                {APP_SESSION_TABLE}
            WHERE
                session_id = ?
            LIMIT 1
            """,
            (
                session_id,
            ),
        ).fetchone()

    return row is not None


def switch_session(
    session_id: str,
):
    """
    切换到历史 Session。

    返回：
        session, session_id
    """

    session_id = (
        session_id or ""
    ).strip()

    if not session_id:

        raise ValueError(
            "Session ID 不能为空。"
        )

    if not session_exists(
        session_id
    ):

        raise ValueError(
            "指定的 Session 不存在。"
        )

    session = _make_session(
        session_id
    )

    _write_current_session_id(
        session_id
    )

    touch_session(
        session_id
    )

    return (
        session,
        session_id,
    )


def rename_session(
    session_id: str,
    title: str,
):
    """
    修改会话名称。
    """

    title = (
        title or ""
    ).strip()

    if not title:

        raise ValueError(
            "会话名称不能为空。"
        )

    # 防止 UI 出现超长名称
    title = title[:80]

    if not session_exists(
        session_id
    ):

        raise ValueError(
            "指定的 Session 不存在。"
        )

    with _connect() as conn:

        conn.execute(
            f"""
            UPDATE
                {APP_SESSION_TABLE}

            SET
                title = ?,
                updated_at =
                    CURRENT_TIMESTAMP

            WHERE
                session_id = ?
            """,
            (
                title,
                session_id,
            ),
        )

        conn.commit()

    return title


# ============================================================
# 删除会话
# ============================================================

async def delete_session(
    session_id: str,
):
    """
    删除指定 Session。

    这里只负责真正删除：
    1. Agents SDK Session 数据
    2. app_conversations 元数据

    不在这里创建新 Session。
    新 Session 由 AgentService 统一管理。
    """

    session_id = (
        session_id or ""
    ).strip()

    if not session_id:

        raise ValueError(
            "Session ID 不能为空。"
        )

    if not session_exists(
        session_id
    ):

        raise ValueError(
            "指定的 Session 不存在。"
        )

    current_session_id = (
        _read_current_session_id()
    )

    # --------------------------------------------
    # 删除 Agents SDK 自己保存的历史
    # --------------------------------------------

    session = _make_session(
        session_id
    )

    try:

        await session.clear_session()

    finally:

        try:
            session.close()
        except Exception:
            pass

    # --------------------------------------------
    # 删除我们自己的 UI Metadata
    # --------------------------------------------

    with _connect() as conn:

        conn.execute(
            f"""
            DELETE FROM
                {APP_SESSION_TABLE}
            WHERE
                session_id = ?
            """,
            (
                session_id,
            ),
        )

        conn.commit()

    # --------------------------------------------
    # 如果删除的是当前 Session，
    # 清空 current_session.txt。
    #
    # 下一步由 AgentService 创建新的 Session。
    # --------------------------------------------

    if (
        current_session_id
        == session_id
    ):

        try:

            CURRENT_SESSION_FILE.write_text(
                "",
                encoding="utf-8",
            )

        except Exception:
            pass

    return True

# ============================================================
# Chat History
# ============================================================

def _content_to_text(
    content: Any,
) -> str:
    """
    将 Agents SDK Message Content
    转成 Chatbot 可以显示的纯文本。
    """

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
        return ""

    texts = []

    for part in content:

        if not isinstance(
            part,
            dict,
        ):
            continue

        part_type = part.get(
            "type",
            ""
        )

        if part_type not in {
            "input_text",
            "output_text",
            "text",
        }:
            continue

        text = part.get(
            "text",
            ""
        )

        if text:
            texts.append(
                str(text)
            )

    return "\n".join(
        texts
    )


async def get_chat_history(
    session_id: str,
    limit: int | None = None,
):
    """
    获取历史会话并转换成 Gradio Chatbot messages。

    这里只显示：
        user
        assistant

    Tool Call / Tool Output
    不直接塞进聊天区域；
    它们仍由右侧执行轨迹显示。
    """

    if not session_exists(
        session_id
    ):

        return []

    session = _make_session(
        session_id
    )

    try:

        items = await (
            session.get_items(
                limit=limit
            )
        )

    finally:

        try:
            session.close()
        except Exception:
            pass

    return chat_messages_from_items(items)


def chat_messages_from_items(items):
    """Build display history without losing final answers returned by finish_task."""
    messages = []
    finish_calls = set()
    turn_start = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and item.get("name") == "finish_task" and item.get("call_id"):
            finish_calls.add(item.get("call_id"))
            continue
        if item.get("type") == "function_call_output" and item.get("call_id") in finish_calls:
            answer = _content_to_text(item.get("output"))
            if answer:
                messages[turn_start:] = [{"role": "assistant", "content": answer}]
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _content_to_text(item.get("content"))
        if not text:
            continue
        if role == "user":
            finish_calls.clear()
            messages.append({"role": role, "content": text})
            turn_start = len(messages)
        else:
            messages.append({"role": role, "content": text})

    return messages


# ============================================================
# 初始化
# ============================================================

_init_app_schema()