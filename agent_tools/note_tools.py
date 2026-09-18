from agents.decorators import tool

from paths import NOTES_FILE

from tool_logging import (
    start_tool_log,
    finish_tool_log,
    error_tool_log,
)

from tool_errors import (
    tool_error_to_model,
)


@tool(
    failure_error_function=tool_error_to_model
)
def save_note(
    content: str,
) -> str:
    """
    保存用户明确要求记录的内容。

    Args:
        content:
            用户希望保存的具体内容。
    """

    print(
        f"\n[工具调用] "
        f"save_note("
        f"content_length={len(content)})"
    )

    start_time = start_tool_log(
        "save_note",
        {
            "content_length": len(content),
        },
    )

    try:

        with NOTES_FILE.open(
            "a",
            encoding="utf-8",
        ) as file:

            file.write(
                content + "\n"
            )

        result = (
            "笔记已成功保存。"
        )

        finish_tool_log(
            "save_note",
            start_time,
            result,
        )

        return result

    except Exception as e:

        error_tool_log(
            "save_note",
            start_time,
            e,
        )

        raise


@tool(
    failure_error_function=tool_error_to_model
)
def read_notes() -> str:
    """
    读取用户之前保存的所有笔记。
    """

    print(
        "\n[工具调用] read_notes()"
    )

    start_time = start_tool_log(
        "read_notes",
        {},
    )

    try:

        if not NOTES_FILE.exists():

            result = (
                "目前还没有保存任何笔记。"
            )

            finish_tool_log(
                "read_notes",
                start_time,
                {
                    "exists": False,
                    "content_length": 0,
                },
            )

            return result

        content = NOTES_FILE.read_text(
            encoding="utf-8",
        )

        if not content.strip():

            result = (
                "目前还没有保存任何笔记。"
            )

            finish_tool_log(
                "read_notes",
                start_time,
                {
                    "exists": True,
                    "content_length": 0,
                },
            )

            return result

        finish_tool_log(
            "read_notes",
            start_time,
            {
                "exists": True,
                "content_length": len(content),
            },
        )

        return content

    except Exception as e:

        error_tool_log(
            "read_notes",
            start_time,
            e,
        )

        raise