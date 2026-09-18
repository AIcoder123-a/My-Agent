from pathlib import Path

from agents.decorators import tool

from paths import WORKSPACE_DIR


def safe_workspace_path(relative_path: str) -> Path:
    """
    将相对路径转换成 workspace 中的安全绝对路径。

    如果路径试图逃离 workspace，则拒绝访问。
    """

    workspace = WORKSPACE_DIR.resolve()

    target = (
        workspace / relative_path
    ).resolve()

    try:
        target.relative_to(workspace)

    except ValueError:
        raise ValueError(
            "禁止访问 workspace 目录之外的文件。"
        )

    return target


@tool
def list_files(
    directory: str = "."
) -> str:
    """
    查看 workspace 中指定目录里的文件和文件夹。

    Args:
        directory:
            相对于 workspace 的目录。
            默认为 "."，表示 workspace 根目录。
    """

    print(
        f"\n[工具调用] "
        f"list_files(directory={directory})"
    )

    target = safe_workspace_path(
        directory
    )

    if not target.exists():
        return "指定目录不存在。"

    if not target.is_dir():
        return "指定路径不是目录。"

    items = []

    for item in sorted(target.iterdir()):

        relative = item.relative_to(
            WORKSPACE_DIR
        )

        if item.is_dir():
            items.append(
                f"[目录] {relative}"
            )

        else:
            items.append(
                f"[文件] {relative}"
            )

    if not items:
        return "这个目录目前是空的。"

    return "\n".join(items)


@tool
def read_file(
    path: str
) -> str:
    """
    读取 workspace 中指定文本文件的内容。

    Args:
        path:
            相对于 workspace 的文件路径。
            例如 "test.py" 或 "docs/readme.txt"。
    """

    print(
        f"\n[工具调用] "
        f"read_file(path={path})"
    )

    target = safe_workspace_path(
        path
    )

    if not target.exists():
        return "文件不存在。"

    if not target.is_file():
        return "指定路径不是文件。"

    try:

        return target.read_text(
            encoding="utf-8"
        )

    except UnicodeDecodeError:

        return (
            "该文件不是 UTF-8 文本文件，"
            "当前 read_file 无法读取。"
        )


@tool
def write_file(
    path: str,
    content: str,
) -> str:
    """
    将文本写入 workspace 中的指定文件。

    文件不存在时会创建；
    文件已存在时会覆盖原内容。

    Args:
        path:
            相对于 workspace 的文件路径。
        content:
            要写入文件的完整文本内容。
    """

    print(
        f"\n[工具调用] "
        f"write_file(path={path})"
    )

    target = safe_workspace_path(
        path
    )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    target.write_text(
        content,
        encoding="utf-8",
    )

    return (
        f"文件已成功写入：{path}"
    )