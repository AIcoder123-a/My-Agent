from tool_errors import (
    tool_error_to_model,
)
from tool_logging import (
    start_tool_log,
    finish_tool_log,
    error_tool_log,
)
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


@tool(
    failure_error_function=tool_error_to_model
)
def list_files(
    path: str = ".",
) -> str:
    """
    列出 workspace 中指定目录的文件。
    """

    print(
        f"\n[工具调用] "
        f"list_files(path={path})"
    )

    start_time = start_tool_log(
        "list_files",
        {
            "path": path,
        },
    )

    try:
        target = safe_workspace_path(
            path
        )

        if not target.exists():
            raise FileNotFoundError(
                f"目录不存在：{path}"
            )

        if not target.is_dir():
            raise NotADirectoryError(
                f"不是目录：{path}"
            )

        items = []

        for item in sorted(
            target.iterdir(),
            key=lambda p: p.name.lower(),
        ):
            item_type = (
                "目录"
                if item.is_dir()
                else "文件"
            )

            items.append(
                f"[{item_type}] {item.name}"
            )

        if items:
            result = "\n".join(items)
        else:
            result = "目录为空。"

        finish_tool_log(
            "list_files",
            start_time,
            {
                "path": path,
                "item_count": len(items),
            },
        )

        return result

    except Exception as e:
        error_tool_log(
            "list_files",
            start_time,
            e,
        )

        raise


@tool(
    failure_error_function=tool_error_to_model
)
def read_file(
    path: str,
) -> str:
    """
    读取 workspace 中的文本文件。
    """

    print(
        f"\n[工具调用] "
        f"read_file(path={path})"
    )

    start_time = start_tool_log(
        "read_file",
        {
            "path": path,
        },
    )

    try:
        target = safe_workspace_path(
            path
        )

        with target.open("r", encoding="utf-8") as stream:
            content = stream.read(120001)
        if len(content) > 120000:
            content = content[:120000] + (
                "\n\n[内容已截断。请使用 read_file_lines 按行阅读，或使用终端处理大文件。]"
            )

        finish_tool_log(
            "read_file",
            start_time,
            {
                "path": path,
                "content_length": len(content),
            },
        )

        return content

    except Exception as e:
        error_tool_log(
            "read_file",
            start_time,
            e,
        )

        raise



@tool(
    needs_approval=True,
    failure_error_function=tool_error_to_model,
)
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

    start_time = start_tool_log(
        "write_file",
        {
            "path": path,
            "content_length": len(content),
        },
    )

    try:
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

        result = (
            f"文件已成功写入：{path}"
        )

        finish_tool_log(
            "write_file",
            start_time,
            result,
        )

        return result

    except Exception as e:
        error_tool_log(
            "write_file",
            start_time,
            e,
        )

        raise
