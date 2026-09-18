from agents.decorators import tool


@tool
def calculator(
    a: float,
    b: float,
    operation: str,
) -> float:
    """
    执行基础数学运算。

    Args:
        a: 第一个数字。
        b: 第二个数字。
        operation:
            add、subtract、multiply 或 divide。
    """

    print(
        f"\n[工具调用] calculator("
        f"a={a}, "
        f"b={b}, "
        f"operation={operation})"
    )

    if operation == "add":
        return a + b

    if operation == "subtract":
        return a - b

    if operation == "multiply":
        return a * b

    if operation == "divide":

        if b == 0:
            raise ValueError("除数不能为 0")

        return a / b

    raise ValueError(
        f"不支持的 operation：{operation}"
    )