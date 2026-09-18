from typing import Any

from agents import RunContextWrapper


def tool_error_to_model(
    context: RunContextWrapper[Any],
    error: Exception,
) -> str:
    """
    将 Tool 执行错误转换为模型可理解的错误信息。

    不向模型暴露完整 traceback、
    环境变量等内部敏感信息。
    """

    if isinstance(
        error,
        FileNotFoundError,
    ):
        return (
            "TOOL_ERROR: FILE_NOT_FOUND. "
            "请求的文件或目录不存在。"
            "请检查路径。"
            "必要时先使用 list_files "
            "查看实际存在的文件，"
            "然后重新选择正确路径。"
        )

    if isinstance(
        error,
        PermissionError,
    ):
        return (
            "TOOL_ERROR: PERMISSION_DENIED. "
            "当前操作没有权限。"
            "不要尝试绕过 workspace "
            "权限边界，请调整方案。"
        )

    if isinstance(
        error,
        IsADirectoryError,
    ):
        return (
            "TOOL_ERROR: IS_DIRECTORY. "
            "当前路径是目录，"
            "不是可以直接读取的文件。"
            "请使用 list_files 查看目录。"
        )

    if isinstance(
        error,
        NotADirectoryError,
    ):
        return (
            "TOOL_ERROR: NOT_DIRECTORY. "
            "当前路径不是目录。"
            "请检查路径并重新选择操作。"
        )

    if isinstance(
        error,
        ZeroDivisionError,
    ):
        return (
            "TOOL_ERROR: DIVISION_BY_ZERO. "
            "不能除以 0。"
            "请修改计算方案。"
        )

    if isinstance(
        error,
        ValueError,
    ):
        return (
            "TOOL_ERROR: INVALID_VALUE. "
            f"{str(error)[:300]}"
        )

    return (
        "TOOL_ERROR: EXECUTION_FAILED. "
        f"{type(error).__name__}: "
        f"{str(error)[:300]}"
    )