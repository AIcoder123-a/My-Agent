from tool_errors import (
    tool_error_to_model,
)
from tool_logging import (
    start_tool_log,
    finish_tool_log,
    error_tool_log,
)

from agents.decorators import tool

import workspace_snapshots


@tool(
    failure_error_function=tool_error_to_model
)
def list_recent_changes(
    limit: int = 10,
) -> str:
    """
    列出最近对 workspace 文件的改动，用于决定撤销哪一步。

    只记录经过 write_file / edit_file 的改动；
    通过终端修改的文件不在其中。

    Args:
        limit:
            最多显示几条，默认 10。
    """

    print(
        f"\n[工具调用] "
        f"list_recent_changes(limit={limit})"
    )

    start_time = start_tool_log(
        "list_recent_changes",
        {
            "limit": limit,
        },
    )

    try:
        limit = max(
            1,
            min(
                int(limit),
                30,
            ),
        )

        changes = (
            workspace_snapshots.list_changes(
                limit=limit,
            )
        )

        if not changes:
            result = (
                "还没有记录任何文件改动。"
            )

        else:
            lines = []

            for index, change in enumerate(
                changes,
                1,
            ):
                kind = (
                    "修改"
                    if change.get("existed")
                    else "新建"
                )

                lines.append(
                    f"{index}. "
                    f"[{kind}] "
                    f"{change.get('path')} "
                    f"（{change.get('time')}，"
                    f"id={change.get('id')}）"
                )

            result = "\n".join(lines)

        finish_tool_log(
            "list_recent_changes",
            start_time,
            {
                "count": len(changes),
            },
        )

        return result

    except Exception as e:
        error_tool_log(
            "list_recent_changes",
            start_time,
            e,
        )

        raise


@tool(
    needs_approval=True,
    failure_error_function=tool_error_to_model,
)
def undo_last_change() -> str:
    """
    撤销最近一次对 workspace 文件的改动。

    会把文件恢复成上一次写入前的内容；
    如果那次改动是新建文件，则删除该文件。

    只覆盖 write_file / edit_file 的写入，
    终端造成的文件变化无法撤销。
    """

    print(
        "\n[工具调用] "
        "undo_last_change()"
    )

    start_time = start_tool_log(
        "undo_last_change",
        {},
    )

    try:
        result = (
            workspace_snapshots.undo_last()
        )

        finish_tool_log(
            "undo_last_change",
            start_time,
            result,
        )

        return result

    except Exception as e:
        error_tool_log(
            "undo_last_change",
            start_time,
            e,
        )

        raise
