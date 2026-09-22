"""示例插件：给小智加一个文本统计工具。

这是插件系统的样子——一个 .py 文件、一个 function_tool、一个 PLUGIN_TOOLS。
安装后默认不启用；在「工作台 → 插件」里点启用即可生效，无需重启。
"""
from agents import function_tool


PLUGIN_NAME = "示例插件 · 文本统计"
PLUGIN_VERSION = "0.1.0"
PLUGIN_AUTHOR = "小智"
PLUGIN_DESC = (
    "演示插件怎么写：提供一个统计字符数 / 词数 / 行数的工具。"
    "可以直接启用体验，也可以照着它改出自己的插件。"
)
PLUGIN_INSTRUCTIONS = "需要统计文本规模时，优先使用 count_text。"


@function_tool
def count_text(text: str) -> str:
    """统计一段文本的行数、字符数、词数与字节数。

    Args:
        text: 需要统计的文本。
    """
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    characters = len(text)
    words = len([part for part in text.split() if part])
    return (
        f"行数 {lines} · 字符数 {characters} · "
        f"词数 {words} · UTF-8 字节 {len(text.encode('utf-8'))}"
    )


PLUGIN_TOOLS = [count_text]
