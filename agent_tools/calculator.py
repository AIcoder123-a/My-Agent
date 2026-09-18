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
def calculator(
    operation: str,
    a: float,
    b: float,
) -> float:
    """
    执行基本数学运算。
    """

    print(
        f"\n[工具调用] "
        f"calculator("
        f"operation={operation}, "
        f"a={a}, "
        f"b={b})"
    )

    start_time = start_tool_log(
        "calculator",
        {
            "operation": operation,
            "a": a,
            "b": b,
        },
    )

    try:
        operation = operation.lower()

        if operation == "add":
            result = a + b

        elif operation == "subtract":
            result = a - b

        elif operation == "multiply":
            result = a * b

        elif operation == "divide":
            if b == 0:
                raise ValueError(
                    "除数不能为 0"
                )

            result = a / b

        else:
            raise ValueError(
                f"不支持的操作：{operation}"
            )

        finish_tool_log(
            "calculator",
            start_time,
            result,
        )

        return result

    except Exception as e:
        error_tool_log(
            "calculator",
            start_time,
            e,
        )

        raise