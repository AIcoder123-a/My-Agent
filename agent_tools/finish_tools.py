from tool_logging import (
    start_tool_log,
    finish_tool_log,
    error_tool_log,
)
from agents.decorators import tool
@tool
def finish_task(
    summary: str,
) -> str:
    """
    表示当前任务已经完成。
    """

    print(
        f"\n[任务完成] {summary}"
    )

    start_time = start_tool_log(
        "finish_task",
        {
            "summary_length": len(summary),
        },
    )

    try:
        result = summary

        finish_tool_log(
            "finish_task",
            start_time,
            {
                "summary_length": len(summary),
            },
        )

        return result

    except Exception as e:
        error_tool_log(
            "finish_task",
            start_time,
            e,
        )

        raise