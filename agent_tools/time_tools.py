from datetime import datetime

from agents.decorators import tool


@tool
def get_current_time() -> str:
    """
    获取当前电脑所在时区的本地日期和时间。
    当用户询问现在几点、当前日期等问题时使用。
    """

    print(
        "\n[工具调用] get_current_time()"
    )

    now = datetime.now().astimezone()

    return now.strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )