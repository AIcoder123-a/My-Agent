from agents.decorators import tool


@tool
def finish_task(summary: str) -> str:
    """
    当用户要求的整个任务已经真正完成时调用。

    只有在所有必要的读取、分析、写入等操作都完成后，
    才能调用此工具。

    Args:
        summary:
            简要说明已经完成了什么，
            包括生成或修改了哪些文件。
    """

    print(
        f"\n[任务完成] {summary}"
    )

    return summary