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
    结束任务并将 summary 作为用户看到的最终答案。

    Args:
        summary: 完整最终答案，包含必要的结论、代码和来源；不要传入计划或思考过程。
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