import uuid

from agents import SQLiteSession

from paths import (
    DATABASE_FILE,
    CURRENT_SESSION_FILE,
)


AGENT_VERSION = "v2"


def generate_session_id() -> str:
    """
    生成新的 Session ID。
    """

    return (
        f"{AGENT_VERSION}_"
        f"{uuid.uuid4()}"
    )


def save_current_session_id(
    session_id: str
) -> None:
    """
    保存当前 Session ID。
    """

    CURRENT_SESSION_FILE.write_text(
        session_id,
        encoding="utf-8",
    )


def load_current_session_id() -> str | None:
    """
    读取上一次使用的 Session ID。
    """

    if not CURRENT_SESSION_FILE.exists():
        return None

    session_id = (
        CURRENT_SESSION_FILE
        .read_text(encoding="utf-8")
        .strip()
    )

    if not session_id:
        return None

    return session_id


def create_session(
    session_id: str
) -> SQLiteSession:
    """
    根据 Session ID 创建 SQLiteSession。
    """

    return SQLiteSession(
        session_id,
        DATABASE_FILE,
    )


def load_or_create_session():
    """
    启动程序时：
    有旧 Session 就继续；
    没有就创建新的。
    """

    session_id = load_current_session_id()

    if session_id is None:
        session_id = generate_session_id()

        save_current_session_id(
            session_id
        )

    session = create_session(
        session_id
    )

    return session, session_id


def create_new_session():
    """
    创建一段全新的对话。
    """

    session_id = generate_session_id()

    save_current_session_id(
        session_id
    )

    session = create_session(
        session_id
    )

    return session, session_id