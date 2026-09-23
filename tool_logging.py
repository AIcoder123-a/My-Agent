import json
import os
import time
import uuid

from collections import deque
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent

LOG_FILE = (
    PROJECT_ROOT
    / "data"
    / "tool_audit.jsonl"
)


# ============================================================
# 日志轮转
#
# 审计日志原本只增不减：
# 每调一次工具就写一行，长期运行会无限增长，
# 而 read_audit_logs 又是整文件读取 —— 越大越慢，
# 每次刷新「能力 → 审计」都要拖一遍全量。
#
# 这里按体积轮转，保留若干份历史分片。
# ============================================================

# 单个日志文件超过这个体积就轮转
MAX_LOG_BYTES = 8 * 1024 * 1024

# 保留多少个历史分片（不含当前文件）
MAX_LOG_BACKUPS = 3

# 每写多少条检查一次体积。
# 每条都 stat 一次没必要，工具调用很频繁。
SIZE_CHECK_INTERVAL = 50

_rotate_lock = Lock()

_write_count = 0


def _rotate_if_needed() -> None:
    """
    当前日志超限时轮转到历史分片。
    """

    global _write_count

    _write_count += 1

    if (
        _write_count
        % SIZE_CHECK_INTERVAL
    ):
        return

    try:

        if (
            not LOG_FILE.exists()
            or LOG_FILE.stat().st_size
            < MAX_LOG_BYTES
        ):
            return

    except OSError:
        return

    with _rotate_lock:

        # 双重检查：
        # 上一把锁期间可能已经被别的线程轮转过。
        try:

            if (
                not LOG_FILE.exists()
                or (
                    LOG_FILE.stat()
                    .st_size
                )
                < MAX_LOG_BYTES
            ):
                return

        except OSError:
            return

        try:

            # 最老的一份直接丢弃
            oldest = (
                LOG_FILE.with_name(
                    f"{LOG_FILE.stem}"
                    f".{MAX_LOG_BACKUPS}"
                    f"{LOG_FILE.suffix}"
                )
            )

            if oldest.exists():
                oldest.unlink()

            # 其余分片依次后退一位
            for index in range(
                MAX_LOG_BACKUPS - 1,
                0,
                -1,
            ):

                source = (
                    LOG_FILE.with_name(
                        f"{LOG_FILE.stem}"
                        f".{index}"
                        f"{LOG_FILE.suffix}"
                    )
                )

                target = (
                    LOG_FILE.with_name(
                        f"{LOG_FILE.stem}"
                        f".{index + 1}"
                        f"{LOG_FILE.suffix}"
                    )
                )

                if source.exists():
                    os.replace(
                        source,
                        target,
                    )

            # 当前文件成为 .1
            first = (
                LOG_FILE.with_name(
                    f"{LOG_FILE.stem}.1"
                    f"{LOG_FILE.suffix}"
                )
            )

            os.replace(
                LOG_FILE,
                first,
            )

            # 必须立即重建一个空的当前文件。
            #
            # 否则在当前文件被移走、下一条日志写入之前的这段时间里：
            #   - read_audit_logs 看不到任何记录，
            #     UI 上表现为「审计日志被清空了」；
            #   - 如果此后不再写日志，当前文件就一直不存在。
            LOG_FILE.open(
                "a",
                encoding="utf-8",
            ).close()

        except OSError:
            # 轮转失败不应该影响正常写日志
            pass


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

    _rotate_if_needed()


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


def _archive_path(
    index: int,
) -> Path:

    return LOG_FILE.with_name(
        f"{LOG_FILE.stem}.{index}"
        f"{LOG_FILE.suffix}"
    )


def _read_log_file(
    path: Path,
    task_id: str | None,
    records: deque,
) -> None:

    try:

        file = path.open(
            "r",
            encoding="utf-8",
        )

    except OSError:
        return

    with file:

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


def read_audit_logs(
    limit: int = 200,
    task_id: str | None = None,
    include_archives: bool = False,
) -> list[dict]:
    """
    读取最近的 Audit Log。

    可以指定 task_id，只查看某一次任务。

    include_archives=True 时，
    当前日志不足 limit 会继续读轮转出来的历史分片。

    用 deque(maxlen=limit) 而不是先收集全部再切片：
    日志文件可以有几 MB，没必要把整份都常驻内存。
    """

    if not LOG_FILE.exists():
        return []

    records: deque = deque(
        maxlen=limit
    )

    files = []

    if include_archives:

        # 从最老的分片开始读，
        # deque 才会把最新的留在队列里。
        for index in range(
            MAX_LOG_BACKUPS,
            0,
            -1,
        ):

            files.append(
                _archive_path(
                    index
                )
            )

    files.append(
        LOG_FILE
    )

    for path in files:

        if not path.exists():
            continue

        _read_log_file(
            path,
            task_id,
            records,
        )

    return list(
        records
    )