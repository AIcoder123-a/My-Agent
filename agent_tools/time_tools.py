from tool_errors import (
    tool_error_to_model,
)
from tool_logging import (
    start_tool_log,
    finish_tool_log,
    error_tool_log,
)
from agents.decorators import tool
@tool(
    failure_error_function=tool_error_to_model
)
def get_current_time() -> str:
    """
    获取本机当前时间。
    """

    print(
        "\n[工具调用] get_current_time()"
    )

    start_time = start_tool_log(
        "get_current_time",
        {},
    )

    try:
        from datetime import datetime

        result = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        finish_tool_log(
            "get_current_time",
            start_time,
            result,
        )

        return result

    except Exception as e:
        error_tool_log(
            "get_current_time",
            start_time,
            e,
        )

        raise