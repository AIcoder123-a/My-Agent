from agents.decorators import tool

from paths import NOTES_FILE


@tool
def save_note(content: str) -> str:
    """
    保存用户明确要求记录的内容。

    Args:
        content: 用户希望保存的具体内容。
    """

    print(
        f"\n[工具调用] "
        f"save_note(content={content})"
    )

    with NOTES_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            content + "\n"
        )

    return "笔记已成功保存。"


@tool
def read_notes() -> str:
    """
    读取用户之前保存的所有笔记。
    """

    print(
        "\n[工具调用] read_notes()"
    )

    if not NOTES_FILE.exists():
        return "目前还没有保存任何笔记。"

    content = NOTES_FILE.read_text(
        encoding="utf-8"
    )

    if not content.strip():
        return "目前还没有保存任何笔记。"

    return content