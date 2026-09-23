"""模型调用量统计。

背景
====

config.py 里调用了 set_tracing_disabled(True)，
之后 Runner 的 Usage 数据没有任何地方接收，直接丢弃。
结果就是：用了多少 token、调了多少次模型，全靠猜。

这里做三件事：
1. 把每次 Runner 的 Usage 累加到「任务 / 会话 / 全局」三个层级；
2. 持久化到 data/usage.json，重启不丢；
3. 提供格式化输出，供 UI 直接展示。

关于精度
========

Usage 是模型服务端返回的真实统计（如果有返回），
比 token_budget 里的字符估算准确得多。
两者用途不同：
    token_budget  -> 事前切分（不知道真实值，只能估）
    usage_stats   -> 事后记账（用真实值）
"""
import json
import os
import tempfile
from pathlib import Path
from threading import Lock


PROJECT_ROOT = Path(
    __file__
).resolve().parent

USAGE_FILE = (
    PROJECT_ROOT
    / "data"
    / "usage.json"
)

_lock = Lock()


# ============================================================
# 存储
# ============================================================

def _empty_stats() -> dict:

    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "tasks": 0,
    }


def _load() -> dict:

    if not USAGE_FILE.exists():
        return {}

    try:

        with USAGE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

        if isinstance(
            data,
            dict,
        ):
            return data

    except (
        OSError,
        ValueError,
    ):
        pass

    return {}


def _save(
    data: dict,
) -> None:

    USAGE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    handle, temp = tempfile.mkstemp(
        dir=str(
            USAGE_FILE.parent
        ),
        suffix=".tmp",
    )

    try:

        with os.fdopen(
            handle,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=(
                    False
                ),
                indent=2,
            )

        os.replace(
            temp,
            USAGE_FILE,
        )

    finally:

        Path(
            temp
        ).unlink(
            missing_ok=True
        )


# ============================================================
# 提取
# ============================================================

def extract_usage(
    usage,
) -> dict:
    """
    从 SDK 的 Usage 对象（或 dict）中提取数值。

    取不到的字段一律按 0 处理：
    用量统计不能因为兼容问题把主流程搞崩。
    """

    def pick(
        name: str,
    ) -> int:

        value = None

        if isinstance(
            usage,
            dict,
        ):
            value = usage.get(
                name
            )

        else:
            value = getattr(
                usage,
                name,
                None,
            )

        try:
            return int(
                value or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            return 0

    return {
        "requests": pick(
            "requests"
        ),
        "input_tokens": pick(
            "input_tokens"
        ),
        "output_tokens": pick(
            "output_tokens"
        ),
        "total_tokens": pick(
            "total_tokens"
        ),
    }


def _accumulate(
    bucket: dict,
    delta: dict,
) -> None:

    for key, value in (
        delta.items()
    ):

        bucket[key] = (
            bucket.get(
                key,
                0
            )
            + value
        )


# ============================================================
# 记录
# ============================================================

def record_usage(
    session_id: str | None,
    usage,
) -> dict:
    """
    累加一次 Runner 的用量，返回本次的增量。

    usage 可以是 Usage 对象，也可以是
    extract_usage 产出的 dict。
    """

    if isinstance(
        usage,
        dict,
    ):

        delta = {
            key: int(
                usage.get(
                    key,
                    0
                )
                or 0
            )
            for key in (
                "requests",
                "input_tokens",
                "output_tokens",
                "total_tokens",
            )
        }

    else:

        delta = extract_usage(
            usage
        )

    if (
        not any(
            delta.values()
        )
    ):
        return delta

    with _lock:

        data = _load()

        total = (
            data.setdefault(
                "__total__",
                _empty_stats(),
            )
        )

        _accumulate(
            total,
            delta,
        )

        if session_id:

            session = (
                data.setdefault(
                    f"session:{session_id}",
                    _empty_stats(),
                )
            )

            _accumulate(
                session,
                delta,
            )

        _save(
            data
        )

    return delta


def count_task(
    session_id: str | None,
) -> None:
    """
    任务结束时 +1，用来算「平均每个任务多少 token」。
    """

    with _lock:

        data = _load()

        for key in (
            "__total__",
            (
                f"session:{session_id}"
                if session_id
                else None
            ),
        ):

            if not key:
                continue

            bucket = (
                data.setdefault(
                    key,
                    _empty_stats(),
                )
            )

            bucket[
                "tasks"
            ] = (
                bucket.get(
                    "tasks",
                    0
                )
                + 1
            )

        _save(
            data
        )


# ============================================================
# 查询
# ============================================================

def get_session_usage(
    session_id: str | None,
) -> dict:

    if not session_id:
        return _empty_stats()

    data = _load()

    return dict(
        data.get(
            f"session:{session_id}",
            _empty_stats(),
        )
    )


def get_total_usage() -> dict:

    data = _load()

    return dict(
        data.get(
            "__total__",
            _empty_stats(),
        )
    )


# ============================================================
# 展示
# ============================================================

def _format_tokens(
    count: int,
) -> str:

    if (
        count
        >= 1_000_000
    ):
        return (
            f"{count / 1_000_000:.2f}M"
        )

    if count >= 1000:
        return (
            f"{count / 1000:.1f}K"
        )

    return str(
        count
    )


def format_usage(
    stats: dict,
) -> str:
    """
    把统计格式化成一段人类可读的 Markdown。
    """

    if not stats:
        return (
            "暂无用量记录"
        )

    total = int(
        stats.get(
            "total_tokens",
            0
        )
        or 0
    )

    if not total:
        return (
            "暂无用量记录"
        )

    lines = [
        f"- 模型请求："
        f"{stats.get('requests', 0)} 次",
        f"- 输入 Token："
        f"{_format_tokens(stats.get('input_tokens', 0))}",
        f"- 输出 Token："
        f"{_format_tokens(stats.get('output_tokens', 0))}",
        f"- 合计 Token："
        f"{_format_tokens(total)}",
    ]

    tasks = int(
        stats.get(
            "tasks",
            0
        )
        or 0
    )

    if tasks:
        lines.append(
            f"- 完成任务："
            f"{tasks} 个"
            f"（均摊 "
            f"{_format_tokens(total // tasks)}"
            f" / 任务）"
        )

    return "\n".join(
        lines
    )
