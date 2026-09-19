import json
import time
import uuid

from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent

LOG_FILE = (
    PROJECT_ROOT
    / "data"
    / "tool_audit.jsonl"
)


current_session_id: ContextVar[str | None] = ContextVar(
    "current_session_id",
    default=None,
)

current_task_id: ContextVar[str | None] = ContextVar(
    "current_task_id",
    default=None,
)


def start_task_context(
    session_id: str,
) -> str:
    """
    为一次新的用户任务创建日志上下文。
    """

    task_id = uuid.uuid4().hex[:8]

    current_session_id.set(
        session_id
    )

    current_task_id.set(
        task_id
    )

    write_log(
        {
            "event": "task_started",
        }
    )

    return task_id


def write_log(
    data: dict[str, Any],
) -> None:
    """
    写入一条 JSONL 审计日志。
    """

    LOG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    record = {
        "time": datetime.now().isoformat(
            timespec="seconds"
        ),
        "session_id": current_session_id.get(),
        "task_id": current_task_id.get(),
        **data,
    }

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            json.dumps(
                record,
                ensure_ascii=False,
            )
            + "\n"
        )


def start_tool_log(
    tool_name: str,
    arguments: dict[str, Any],
) -> float:
    """
    记录 Tool 开始执行。
    """

    write_log(
        {
            "event": "tool_start",
            "tool": tool_name,
            "arguments": arguments,
        }
    )

    return time.perf_counter()


def finish_tool_log(
    tool_name: str,
    start_time: float,
    result: Any = None,
) -> None:
    """
    记录 Tool 成功执行。
    """

    duration_ms = round(
        (
            time.perf_counter()
            - start_time
        )
        * 1000,
        2,
    )

    write_log(
        {
            "event": "tool_success",
            "tool": tool_name,
            "duration_ms": duration_ms,
            "result": str(result)[:1000],
        }
    )


def error_tool_log(
    tool_name: str,
    start_time: float,
    error: Exception,
) -> None:
    """
    记录 Tool 执行异常。
    """

    duration_ms = round(
        (
            time.perf_counter()
            - start_time
        )
        * 1000,
        2,
    )

    write_log(
        {
            "event": "tool_error",
            "tool": tool_name,
            "duration_ms": duration_ms,
            "error_type": type(error).__name__,
            "error": str(error)[:2000],
        }
    )
def set_task_context(
    session_id: str,
    task_id: str,
) -> None:
    """
    恢复已有任务的日志上下文。

    GUI 中审批和原始任务可能运行在不同事件回调中，
    因此需要重新设置 ContextVar。
    """

    current_session_id.set(
        session_id
    )

    current_task_id.set(
        task_id
    )
def sanitize_tool_arguments(
    tool_name: str,
    arguments,
):
    """
    清理用于 Audit Log 的 Tool 参数。

    UI 审批仍然可以显示真实参数，
    但持久日志不保存敏感正文、Token、密码等内容。
    """

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            return {
                "arguments_length": len(arguments),
            }

    if not isinstance(arguments, dict):
        return {
            "arguments_type": (
                type(arguments).__name__
            )
        }

    safe_arguments = {}

    secret_words = {
        "api_key",
        "apikey",
        "token",
        "password",
        "secret",
        "authorization",
    }

    content_words = {
        "content",
        "text",
        "body",
        "note",
        "message",
    }

    for key, value in arguments.items():

        key_lower = str(key).lower()

        if any(
            word in key_lower
            for word in secret_words
        ):
            safe_arguments[key] = (
                "[REDACTED]"
            )

        elif key_lower in content_words:

            safe_arguments[
                f"{key}_length"
            ] = len(str(value))

        elif isinstance(
            value,
            (str, int, float, bool),
        ):
            value_text = str(value)

            if len(value_text) > 300:
                safe_arguments[key] = (
                    value_text[:300]
                    + "...[truncated]"
                )
            else:
                safe_arguments[key] = value

        else:
            safe_arguments[key] = str(
                value
            )[:300]

    return safe_arguments


def read_audit_logs(
    limit: int = 200,
    task_id: str | None = None,
) -> list[dict]:
    """
    读取最近的 Audit Log。

    可以指定 task_id，只查看某一次任务。
    """

    if not LOG_FILE.exists():
        return []

    records = []

    with LOG_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if (
                task_id
                and record.get("task_id")
                != task_id
            ):
                continue

            records.append(record)

    return records[-limit:]