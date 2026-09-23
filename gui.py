import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import gradio as gr
import httpx

from appearance import APPEARANCE_HTML, APPEARANCE_CSS, APPEARANCE_JS, GLASS_CSS
from workspace_io import import_attachments, import_folder, scan_folder, stage_download
from agent_tools.planning_tools import plan_path
from plugins import (
    PLUGIN_TABLE_HEADERS,
    install_plugin,
    plugin_choices,
    plugin_detail_markdown,
    plugin_rows,
    plugin_summary,
    plugin_template,
    set_plugin_enabled,
    uninstall_plugin,
)

from agent_service import AgentService

from app_settings import (
    SETTINGS_PATH,
    delete_model_api_key,
    get_bool_setting,
    get_model_api_key,
    migrate_env_key_to_keyring,
    model_api_key_status,
    read_settings,
    set_model_api_key,
    update_settings,
)

from mcp_manager import (
    delete_mcp_server,
    format_args_text,
    get_mcp_server,
    list_mcp_servers,
    mcp_dependency_status,
    mcp_server_choices,
    mcp_server_rows,
    parse_args_text,
    parse_json_object_text,
    save_mcp_server,
    test_mcp_server,
)

import usage_stats

# ============================================================
# 基础路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
AUDIT_FILE = PROJECT_ROOT / "data" / "tool_audit.jsonl"

WORKSPACE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

# ============================================================
# Theme
# ============================================================

THEME_VARS = {
    # 页面：最底层保持实色，玻璃要"糊"的是它上面的光晕层
    "body_background_fill": "#07070b",
    "body_text_color": "#f2f2f7",
    "body_text_color_subdued": "#8a8a99",
    # 面层：一律半透明，否则组件会挡住背景光晕，玻璃感直接消失
    "background_fill_primary": "rgba(255,255,255,.055)",
    "background_fill_secondary": "rgba(255,255,255,.032)",
    "block_background_fill": "rgba(255,255,255,.055)",
    "block_border_color": "rgba(255,255,255,.10)",
    "block_label_background_fill": "rgba(255,255,255,.05)",
    "block_label_text_color": "#8a8a99",
    "panel_background_fill": "rgba(255,255,255,.055)",
    "panel_border_color": "rgba(255,255,255,.09)",
    "panel_border_width": "1px",
    "border_color_primary": "rgba(255,255,255,.10)",
    "border_color_accent": "rgba(16,163,127,.45)",
    # 输入
    "input_background_fill": "rgba(255,255,255,.045)",
    "input_background_fill_focus": "rgba(255,255,255,.075)",
    "input_border_color": "rgba(255,255,255,.10)",
    "input_border_color_focus": "rgba(16,163,127,.45)",
    "input_placeholder_color": "#65657a",
    "input_shadow": "none",
    "input_shadow_focus": "none",
    # 按钮
    "button_secondary_background_fill": "rgba(255,255,255,.055)",
    "button_secondary_text_color": "#f2f2f7",
    "button_secondary_border_color": "rgba(255,255,255,.10)",
    "button_primary_background_fill": "#10a37f",
    "button_primary_background_fill_hover": "#17bd93",
    "button_primary_text_color": "#03150f",
    "button_primary_border_color": "rgba(255,255,255,.20)",
    # 表格
    "table_border_color": "rgba(255,255,255,.07)",
    "table_even_background_fill": "rgba(255,255,255,.022)",
    "table_odd_background_fill": "rgba(255,255,255,.045)",
    "table_text_color": "#b8b8c6",
    "table_row_focus": "rgba(255,255,255,.075)",
    # 其它
    "code_background_fill": "rgba(8,10,16,.55)",
    "link_text_color": "#17bd93",
    "link_text_color_hover": "#1fcb9e",
    "checkbox_background_color": "rgba(255,255,255,.06)",
    "checkbox_label_background_fill": "rgba(255,255,255,.05)",
    "checkbox_label_text_color": "#f2f2f7",
    "slider_color": "#10a37f",
    "loader_color": "#10a37f",
    "color_accent": "#10a37f",
    "color_accent_soft": "rgba(16,163,127,.14)",
    "stat_background_fill": "rgba(255,255,255,.05)",
    "error_background_fill": "rgba(239,106,120,.12)",
    "error_border_color": "rgba(239,106,120,.38)",
    "error_text_color": "#ef6a78",
    "shadow_drop": "0 8px 28px rgba(0,0,0,.36)",
    "shadow_drop_lg": "0 18px 50px rgba(0,0,0,.42)",
    "shadow_inset": "inset 0 1px 0 rgba(255,255,255,.10)",
}

def build_theme() -> gr.themes.Base:
    """
    显式构造暗色主题。

    Gradio 的 ``Base()`` 默认是浅色，只有浏览器偏好为暗色时才会切到暗色。
    这里把浅色与暗色两套变量写成同一组暗色值，
    保证任何系统环境下渲染结果一致，
    也避免未被我自定义 CSS 覆盖的组件（手风琴、状态遮罩、
    表格、下拉面板等）出现突兀的白底。
    """

    base = gr.themes.Base()
    available = set(vars(base))

    pairs = {}

    for key, value in THEME_VARS.items():

        # 不是每个变量都有 _dark 变体（例如 color_accent），
        # 必须按主题实际拥有的键来写，否则 set() 会直接报错。
        if key in available:
            pairs[key] = value

        dark_key = key + "_dark"

        if dark_key in available:
            pairs[dark_key] = value

    return base.set(**pairs)

theme = build_theme()

# ============================================================
# Agent Service
# ============================================================

service = AgentService()

# ============================================================
# 中文名称映射
# ============================================================

TOOL_NAMES = {
    "list_files": "列出文件",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "calculator": "计算器",
    "get_current_time": "获取当前时间",
    "save_note": "保存笔记",
    "read_notes": "读取笔记",
    "finish_task": "结束任务",
    "web_search": "联网搜索",
    "web_fetch": "读取网页",
    "github_trending": "GitHub 热门",
}

TRACE_HEADERS = [
    "时间",
    "状态",
    "工具",
    "详情",
]

AUDIT_HEADERS = [
    "时间",
    "事件",
    "工具",
    "任务",
    "详情",
]

# ============================================================
# 通用辅助
# ============================================================

def tool_text(
    name: str | None,
) -> str:

    if not name:
        return "未知工具"

    mapped = TOOL_NAMES.get(
        name
    )

    if mapped:
        return mapped

    # MCP 使用服务器前缀避免与内置工具重名。
    # 这里保留真实工具名，方便开发调试，
    # 但在界面上明确标为 MCP 工具。
    lowered = name.lower()

    if (
        lowered.startswith(
            "mcp_"
        )
        and "__" in name
    ):

        # mcp_filesystem__read_text_file
        # → MCP · filesystem · read_text_file
        body = name[
            4:
        ]

        server_part, _, tool_part = (
            body.partition(
                "__"
            )
        )

        return (
            "MCP · "
            f"{server_part} · "
            f"{tool_part}"
        )

    if (
        "__" in name
        or lowered.startswith("mcp_")
        or lowered.startswith("mcp-")
    ):
        return f"MCP · {name}"

    return name

def json_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    if isinstance(
        value,
        str,
    ):
        return value

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )

    except Exception:
        return str(value)

def short_text(
    value: Any,
    limit: int = 180,
) -> str:

    text = json_text(
        value
    ).strip()

    if len(text) > limit:
        return (
            text[:limit]
            + "…"
        )

    return text

def markdown_escape(
    value: Any,
) -> str:
    """
    Sources 短文本最小 Markdown 转义。
    """

    text = str(
        value
        if value is not None
        else ""
    )

    for old, new in (
        ("\\", "\\\\"),
        ("[", "\\["),
        ("]", "\\]"),
        ("*", "\\*"),
        ("_", "\\_"),
        ("`", "\\`"),
    ):
        text = text.replace(
            old,
            new,
        )

    return text

def empty_sources_text() -> str:

    return "本轮任务暂无联网来源。"

def render_sources_markdown(
    sources,
) -> str:
    """
    将 AgentService completed 事件中的 sources
    转为右侧可点击 Markdown。

    fetched=True：
        已真正读取网页正文。

    fetched=False：
        当前只获得搜索结果摘要。
    """

    clean_sources = [
        item
        for item in (
            sources or []
        )
        if isinstance(
            item,
            dict,
        )
    ]

    if not clean_sources:
        return empty_sources_text()

    fetched_count = sum(
        1
        for item in clean_sources
        if item.get("fetched")
    )

    lines = [
        (
            f"**本轮共 {len(clean_sources)} 个来源，"
            f"其中 {fetched_count} 个已读取正文。**"
        ),
        "",
    ]

    visible_sources = (
        clean_sources[:12]
    )

    for index, item in enumerate(
        visible_sources,
        start=1,
    ):

        source_id = (
            item.get("source_id")
            or f"S{index}"
        )

        title = (
            item.get("fetch_title")
            or item.get("title")
            or item.get("hostname")
            or item.get("url")
            or "未命名来源"
        )

        url = str(
            item.get("url")
            or ""
        ).strip()

        hostname = str(
            item.get("hostname")
            or ""
        ).strip()

        publisher = str(
            item.get("publisher")
            or ""
        ).strip()

        published_at = str(
            item.get("published_at")
            or ""
        ).strip()

        fetched = bool(
            item.get("fetched")
        )

        status = (
            "✓ 已读取正文"
            if fetched
            else "仅搜索摘要"
        )

        safe_source_id = (
            markdown_escape(
                source_id
            )
        )

        safe_title = (
            markdown_escape(
                title
            )
        )

        if url.startswith(
            ("http://", "https://")
        ):
            title_text = (
                f"[{safe_title}]({url})"
            )
        else:
            title_text = safe_title

        lines.append(
            f"**{safe_source_id} · "
            f"{title_text}**"
        )

        meta = [
            markdown_escape(
                hostname
            ),
            status,
        ]

        if publisher:
            meta.append(
                markdown_escape(
                    publisher
                )
            )

        if published_at:
            meta.append(
                markdown_escape(
                    published_at
                )
            )

        lines.append(
            " · ".join(
                part
                for part in meta
                if part
            )
        )

        lines.append("")

    hidden_count = (
        len(clean_sources)
        - len(visible_sources)
    )

    if hidden_count > 0:
        lines.append(
            f"_另有 {hidden_count} 个来源未展开显示。_"
        )

    return "\n".join(
        lines
    ).strip()

def current_task_text() -> str:

    task_id = (
        service.get_task_id()
    )

    return (
        task_id
        or "—"
    )
def conversation_choices():
    """
    生成对话历史 Dropdown 选项。

    显示：
        ● 会话名称 · 12 项

    实际传给回调：
        session_id
    """

    sessions = (
        service.list_conversations()
    )

    current_id = (
        service.get_session_id()
    )

    choices = []

    for item in sessions:

        session_id = item.get(
            "session_id",
            "",
        )

        title = (
            item.get("title")
            or "新对话"
        )

        count = (
            item.get(
                "item_count",
                0,
            )
            or 0
        )

        prefix = (
            "● "
            if session_id == current_id
            else ""
        )

        label = (
            f"{prefix}{title}"
            f" · {count} 项"
        )

        choices.append(
            (
                label,
                session_id,
            )
        )

    return choices

def current_conversation_title(
    session_id=None,
):
    """
    获取指定会话名称。
    """

    session_id = (
        session_id
        or service.get_session_id()
    )

    for item in (
        service.list_conversations()
    ):

        if (
            item.get("session_id")
            == session_id
        ):
            return (
                item.get("title")
                or "新对话"
            )

    return "新对话"

def conversation_dropdown_state(
    selected=None,
):
    """
    更新对话历史 Dropdown。
    """

    selected = (
        selected
        or service.get_session_id()
    )

    return gr.Dropdown(
        choices=conversation_choices(),
        value=selected,
    )

# ============================================================
# Gradio 6 组件状态更新
# ============================================================

def button_state(
    interactive: bool,
):
    """
    Gradio 6 推荐方式：
    返回新的组件配置更新已有组件。
    """

    return gr.Button(
        interactive=interactive
    )

def approval_view(
    text: str = "",
    visible: bool = False,
):
    """
    审批卡片的更新状态。

    没有待审批操作时整张卡片隐藏，
    避免界面上长期挂着一块空卡片。
    """

    return gr.update(
        value=text,
        visible=visible,
    )

def approval_button_state(
    interactive: bool,
):
    """
    审批按钮：只在真的有审批时才出现。
    """

    return gr.Button(
        interactive=interactive,
        visible=interactive,
    )

def dropdown_state(
    choices,
    value=None,
):

    return gr.Dropdown(
        choices=choices,
        value=value,
    )

# ============================================================
# 文本异常修复
# ============================================================

def normalize_agent_text(value: Any) -> str:
    """Keep provider text intact, including Markdown, code and intentional line breaks."""
    return "" if value is None else str(value)

# ============================================================
# Chat History
# ============================================================

def clone_history(
    history,
):
    """
    将 Gradio Chatbot history
    统一转换成标准 messages。
    """

    result = []

    for item in (
        history or []
    ):

        if isinstance(
            item,
            dict,
        ):

            role = item.get(
                "role"
            )

            content = item.get(
                "content",
                "",
            )

        else:

            role = getattr(
                item,
                "role",
                None,
            )

            content = getattr(
                item,
                "content",
                "",
            )

        if role not in {
            "user",
            "assistant",
        }:
            continue

        message = {"role": role, "content": content}
        metadata = item.get("metadata") if isinstance(item, dict) else getattr(item, "metadata", None)
        if metadata:
            message["metadata"] = dict(metadata)
        result.append(message)

    return result

# ============================================================
# Trace
# ============================================================

def progress_message(content="正在理解任务，请稍候…"):
    return {"role": "assistant", "content": content,
            "metadata": {"title": "思考中 · 执行进展", "status": "pending"}}


def add_trace(
    trace_state,
    *,
    time_text="",
    status="",
    tool="",
    detail="",
):

    trace_state = list(
        trace_state or []
    )

    trace_state.append(
        [
            time_text,
            status,
            tool,
            detail,
        ]
    )

    # 防止 UI 长时间运行无限增长
    if len(trace_state) > 200:

        trace_state = (
            trace_state[-200:]
        )

    return trace_state

# ============================================================
# Workspace
# ============================================================

def workspace_files() -> list[str]:

    files = []

    if not WORKSPACE_DIR.exists():
        return files

    for path in (
        WORKSPACE_DIR.rglob("*")
    ):

        if not path.is_file() or not path.resolve().is_relative_to(WORKSPACE_DIR.resolve()):
            continue

        try:

            relative = (
                path.relative_to(
                    WORKSPACE_DIR
                )
            )

        except ValueError:
            continue

        files.append(
            str(relative).replace(
                "\\",
                "/",
            )
        )

    files.sort()

    return files

def action_feedback(function, message=None, result_index=None):
    """Show the actual action result without changing event output shapes."""
    from functools import wraps
    from inspect import iscoroutinefunction
    def notify(result):
        text = message
        if text is None:
            text = result[result_index] if result_index is not None else result
        if isinstance(text, str) and text.strip():
            text = text.strip()[:220]
            failed = any(word in text for word in ("失败", "请先", "不存在", "不能为空", "未配置", "没有可", "不正确"))
            (gr.Warning if failed else gr.Info)(text)
        return result
    if iscoroutinefunction(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            return notify(await function(*args, **kwargs))
    else:
        @wraps(function)
        def wrapped(*args, **kwargs):
            return notify(function(*args, **kwargs))
    return wrapped

def refresh_workspace():

    files = (
        workspace_files()
    )

    value = (
        files[0]
        if files
        else None
    )

    return dropdown_state(
        choices=files,
        value=value,
    )

def preview_workspace_file(
    relative_path: str | None,
):

    if not relative_path:
        return ""

    candidate = (
        WORKSPACE_DIR
        / relative_path
    ).resolve()

    root = (
        WORKSPACE_DIR.resolve()
    )

    try:

        candidate.relative_to(
            root
        )

    except ValueError:

        return (
            "禁止访问工作区之外的文件。"
        )

    if not candidate.exists():

        return "文件不存在。"

    if not candidate.is_file():

        return "这不是一个文件。"

    try:

        with candidate.open("r", encoding="utf-8") as stream:
            text = stream.read(100001)
        return text[:100000] + ("\n\n[预览已截断，请下载查看完整文件]" if len(text) > 100000 else "")

    except UnicodeDecodeError:

        return (
            "这是二进制文件，"
            "当前预览器不能直接显示。"
        )

    except Exception as error:

        return (
            f"{type(error).__name__}: "
            f"{error}"
        )

# ============================================================
# Audit Log
# ============================================================

def import_attachments_ui(files, message):
    if service.is_busy():
        raise gr.Error("请等待当前任务结束后导入附件。")
    try:
        paths = import_attachments(files)
    except (ValueError, OSError) as error:
        raise gr.Error(str(error)) from error
    if not paths:
        gr.Warning("请先选择附件，再点击导入。")
        return message, refresh_workspace(), "请先选择附件。", None
    gr.Info(f"已导入 {len(paths)} 个附件，引用已加入输入框。")
    references = "\n\n已上传到工作区的附件（请按需读取）：\n" + "\n".join(paths)
    return (
        (message or "") + references,
        dropdown_state(choices=workspace_files(), value=paths[-1]),
        f"已导入 {len(paths)} 个文件，路径已加入输入框。", None,
    )

def _folder_size_text(total):

    if total >= 1048576:
        return f"{total / 1048576:.1f} MB"

    return f"{max(total, 1) / 1024:.0f} KB"

def scan_folder_ui(raw):

    try:
        info = scan_folder(raw)

    except (ValueError, OSError) as error:
        raise gr.Error(str(error)) from error

    if not info["count"]:
        return "扫描完成：没有可导入的文件（可能已被全部过滤）。"

    text = (
        f"扫描完成：**{info['count']}** 个文件 · "
        f"**{_folder_size_text(info['bytes'])}**"
    )

    if info["skipped"]:
        text += f"\n\n将自动跳过 {info['skipped']} 个敏感或无关文件。"

    if info["over_limit"]:
        text += (
            "\n\n⚠️ 超出上限（300 个 / 200 MB），"
            "请缩小范围后再导入。"
        )

    return text

def import_folder_ui(raw, message):

    if service.is_busy():
        raise gr.Error("请等待当前任务结束后再导入。")

    try:
        root, paths, count, total, skipped = (
            import_folder(raw)
        )

    except (ValueError, OSError) as error:
        raise gr.Error(str(error)) from error

    gr.Info(f"已导入 {count} 个文件。")

    shown = paths[:20]

    references = (
        "\n\n已导入的本地文件夹（请按需读取）：\n"
        + "\n".join(shown)
    )

    if count > len(shown):
        references += f"\n…等共 {count} 个文件"

    note = (
        f"已导入 **{count}** 个文件到 `{root}`"
        f"（{_folder_size_text(total)}），路径已加入输入框。"
    )

    if skipped:
        note += f"\n\n已自动跳过 {skipped} 个敏感/无关文件。"

    return (
        (message or "") + references,
        dropdown_state(
            choices=workspace_files(),
            value=paths[0] if paths else None,
        ),
        note,
    )

def workspace_summary_markdown() -> str:
    """左栏工作区卡片：文件数 + 完整路径（超长时保留末两段）。"""

    count = len(
        workspace_files()
    )

    tail = (
        f"{count} 个文件"
        if count
        else "暂无文件"
    )

    full = str(
        WORKSPACE_DIR
    )

    # 路径太长时保留最后两段（如 …\myagent-clean\workspace）：
    # 末段才是"这是哪个目录"的关键信息，中间那截最没用。
    parts = [
        piece
        for piece in full.replace(
            "\\", "/"
        ).split("/")
        if piece
    ]

    short = full

    if len(full) > 30 and len(parts) >= 2:
        short = "…/" + "/".join(
            parts[-2:]
        )

    return (
        '<div class="side-path">'
        '<span class="side-path-name">workspace/</span>'
        f'<span class="side-path-count">{tail}</span>'
        "</div>"
        f'<div class="side-path-full" title="{html.escape(full)}">'
        f'{html.escape(short)}'
        "</div>"
    )

def open_workspace_folder():
    """在系统文件管理器里打开工作区目录。"""

    try:
        if os.name == "nt":
            os.startfile(
                str(
                    WORKSPACE_DIR
                )
            )
        elif sys.platform == "darwin":
            subprocess.Popen(
                [
                    "open",
                    str(
                        WORKSPACE_DIR
                    ),
                ]
            )
        else:
            subprocess.Popen(
                [
                    "xdg-open",
                    str(
                        WORKSPACE_DIR
                    ),
                ]
            )

    except Exception as error:
        raise gr.Error(
            f"无法打开工作区文件夹：{error}"
        )

    gr.Info(
        "已在文件管理器中打开工作区。"
    )

    return (
        "已在文件管理器中打开工作区。"
    )

# ============================================================
# 插件
# ============================================================

def plugin_summary_markdown() -> str:
    """插件概览。返回 HTML，左右两栏共用。"""

    enabled, total = (
        plugin_summary()
    )

    if not total:
        return (
            '<div class="side-stat">'
            "还没有插件。在「插件」页装一个，"
            "启用后立刻生效，无需重启。"
            "</div>"
        )

    return (
        '<div class="side-stat">'
        f"<b>{enabled}</b> / {total} 个已启用"
        "</div>"
    )

def plugin_help_markdown() -> str:

    return (
        "插件就是一个放在 `plugins/` 目录里的 `.py` 文件：\n\n"
        "```python\n"
        + plugin_template()
        + "\n```\n\n"
        "要点：\n\n"
        "- `PLUGIN_TOOLS` 里的工具会直接交给模型使用；\n"
        "- `PLUGIN_INSTRUCTIONS` 会追加到提示词；\n"
        "- 安装后默认**不启用**，确认来源可信再开启；\n"
        "- 插件是本机 Python 代码，启用即拥有与小智相同的权限。"
    )

def _plugin_refresh(
    selected=None,
):

    return (
        dropdown_state(
            choices=plugin_choices(),
            value=selected,
        ),
        plugin_rows(),
        plugin_summary_markdown(),
    )

def refresh_plugins_ui():

    return (
        _plugin_refresh()
    )

def select_plugin_ui(
    plugin_id,
):

    return (
        plugin_detail_markdown(
            plugin_id
        )
    )

def enable_plugin_ui(plugin_id):

    if not plugin_id:
        raise gr.Error(
            "请先选择一个插件。"
        )

    ok, message = (
        set_plugin_enabled(
            plugin_id,
            True,
        )
    )

    if not ok:
        raise gr.Error(message)

    gr.Info(message)

    return (
        (message,)
        + _plugin_refresh(
            plugin_id
        )
        + (
            plugin_detail_markdown(
                plugin_id
            ),
        )
    )

def disable_plugin_ui(plugin_id):

    if not plugin_id:
        raise gr.Error(
            "请先选择一个插件。"
        )

    ok, message = (
        set_plugin_enabled(
            plugin_id,
            False,
        )
    )

    if not ok:
        raise gr.Error(message)

    gr.Info(message)

    return (
        (message,)
        + _plugin_refresh(
            plugin_id
        )
        + (
            plugin_detail_markdown(
                plugin_id
            ),
        )
    )

def uninstall_plugin_ui(plugin_id):

    if not plugin_id:
        raise gr.Error(
            "请先选择一个插件。"
        )

    ok, message = (
        uninstall_plugin(
            plugin_id
        )
    )

    if not ok:
        raise gr.Error(message)

    gr.Info(message)

    return (
        (message,)
        + _plugin_refresh()
        + (
            "插件已卸载，"
            "请重新选择一个查看详情。",
        )
    )

def install_plugin_ui(file):

    ok, message, plugin_id = (
        install_plugin(
            file
        )
    )

    if not ok:
        raise gr.Error(message)

    gr.Info(message)

    return (
        (message,)
        + _plugin_refresh(
            plugin_id
        )
        + (
            plugin_detail_markdown(
                plugin_id
            ),
        )
    )

def download_workspace_ui(path):
    if not path:
        raise gr.Error("请先选择一个工作区文件，再准备下载。")
    try:
        result = stage_download(path)
        gr.Info("文件已准备好，请点击下方下载成果中的文件名。")
        return result
    except (ValueError, OSError) as error:
        raise gr.Error(str(error)) from error

def task_plan_markdown():
    try:
        data = json.loads(plan_path(service.get_session_id()).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "### 任务计划\n复杂任务开始后，小智会在这里记录步骤。"
    symbols = {"pending": "○", "in_progress": "◉", "completed": "✓"}
    lines = ["### 最近任务计划", "步骤由小智更新；任务是否结束请以运行状态为准。", ""]
    for step in data.get("steps", []):
        lines.append(f"- {symbols.get(step.get('status'), '○')} {markdown_escape(step.get('title', ''))}")
    return "\n".join(lines)

def load_audit_rows(
    limit: int = 100,
):

    if not AUDIT_FILE.exists():
        return []

    try:

        lines = (
            AUDIT_FILE
            .read_text(
                encoding="utf-8"
            )
            .splitlines()
        )

    except Exception:
        return []

    rows = []

    for line in lines[-limit:]:

        line = line.strip()

        if not line:
            continue

        try:
            item = json.loads(
                line
            )

        except Exception:
            continue

        detail = ""

        if "error" in item:

            detail = str(
                item.get(
                    "error",
                    "",
                )
            )

        elif "arguments" in item:

            detail = short_text(
                item.get(
                    "arguments"
                )
            )

        elif "output_length" in item:

            detail = (
                "输出长度："
                f"{item.get('output_length')}"
            )

        rows.append(
            [
                item.get(
                    "time",
                    item.get(
                        "timestamp",
                        "",
                    ),
                ),
                item.get(
                    "event",
                    "",
                ),
                tool_text(
                    item.get(
                        "tool"
                    )
                ),
                item.get(
                    "task_id",
                    "",
                ),
                detail,
            ]
        )

    return rows

def refresh_audit():

    return load_audit_rows()

# ============================================================
# Streaming 核心渲染
# ============================================================

async def render_service_stream(
    stream,
    history,
    trace_state,
):

    history = clone_history(
        history
    )

    trace_state = list(
        trace_state or []
    )

    status_text = (
        "正在启动任务…"
    )

    approval_text = (
        approval_view()
    )

    sources_text = (
        empty_sources_text()
    )

    # Everything after the latest user message belongs to this task.
    turn_start = next((i + 1 for i in range(len(history) - 1, -1, -1)
                       if history[i].get("role") == "user"), len(history))
    streamed_text = ""

    # --------------------------------------------------------
    # 停止按钮可用性
    #
    # 只在任务确实在跑时才可点。
    # 一旦进入终态就永久关闭，
    # 避免在这个流里被后续事件反复开关。
    # --------------------------------------------------------

    stop_enabled = True

    # --------------------------------------------------------
    # UI Streaming 节流
    # --------------------------------------------------------

    last_ui_flush = 0.0

    flush_interval = 0.04

    async for event in stream:

        event_code = (
            event.get(
                "event"
            )
        )

        terminal_status = (
            event.get(
                "status"
            )
        )

        force_flush = False

        # ====================================================
        # Task Started
        # ====================================================

        if (
            event_code
            == "task_started"
        ):

            status_text = (
                "正在运行"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="任务开始",
                detail=(
                    "任务 ID："
                    f"{event.get('task_id', '')}"
                ),
            )

            force_flush = True
        # ====================================================
        # Context Compaction
        # ====================================================

        elif (
            event_code
            == "context_compaction_started"
        ):

            status_text = (
                "正在整理长对话上下文…"
            )

            trace_state = add_trace(
                trace_state,

                time_text=event.get(
                    "time",
                    "",
                ),

                status="上下文整理",

                tool="上下文",

                detail=(
                    "正在压缩 "
                    f"{event.get('new_summary_items', 0)} "
                    "个较旧历史项"
                ),
            )

            force_flush = True

        elif (
            event_code
            == "context_compaction_completed"
        ):

            status_text = (
                "上下文整理完成，"
                "正在继续任务…"
            )

            trace_state = add_trace(
                trace_state,

                time_text=event.get(
                    "time",
                    "",
                ),

                status="整理完成",

                tool="上下文",

                detail=(
                    "已摘要历史："
                    f"{event.get('summarized_items', 0)} 项"
                ),
            )

            force_flush = True

        elif (
            event_code
            == "context_compaction_failed"
        ):

            status_text = (
                "上下文整理失败，"
                "将继续正常执行任务…"
            )

            trace_state = add_trace(
                trace_state,

                time_text=event.get(
                    "time",
                    "",
                ),

                status="整理失败",

                tool="上下文",

                detail=short_text(
                    event.get(
                        "error",
                        "",
                    )
                ),
            )

            force_flush = True
        # ====================================================
        # Text Delta
        # ====================================================

        elif (
            event_code
            == "text_delta"
        ):

            delta = event.get("delta", "")
            if delta:
                streamed_text += delta
                history[turn_start:] = [progress_message(streamed_text)]
                status_text = "思考中 · 正在组织回答…"

        # ====================================================
        # Agent Updated
        # ====================================================

        elif (
            event_code
            == "agent_updated"
        ):

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="智能体更新",
                detail=event.get(
                    "agent_name",
                    "",
                ),
            )

            force_flush = True

        # ====================================================
        # MCP Runtime
        # ====================================================

        elif (
            event_code
            in {
                "mcp_connecting",
                "mcp_reconnecting",
            }
        ):

            server_name = str(
                event.get(
                    "server",
                    "MCP",
                )
            )

            status_text = (
                "正在连接 MCP："
                f"{server_name}"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="连接中",
                tool="MCP",
                detail=server_name,
            )

            force_flush = True

        elif (
            event_code
            in {
                "mcp_connected",
                "mcp_reconnected",
            }
        ):

            server_name = str(
                event.get(
                    "server",
                    "MCP",
                )
            )

            status_text = "正在运行"

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="已连接",
                tool="MCP",
                detail=server_name,
            )

            force_flush = True

        elif (
            event_code
            == "mcp_connection_failed"
        ):

            server_name = str(
                event.get(
                    "server",
                    "MCP",
                )
            )

            status_text = (
                "部分 MCP 连接失败，"
                "正在使用其余可用能力继续…"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="连接失败",
                tool="MCP",
                detail=(
                    f"{server_name} · "
                    f"{short_text(event.get('error', ''))}"
                ),
            )

            force_flush = True

        # ====================================================
        # Tool Started
        # ====================================================

        elif (
            event_code
            == "tool_started"
        ):

            if streamed_text:
                trace_state = add_trace(trace_state, time_text=event.get("time", ""),
                                        status="进度", tool="智能体", detail=streamed_text)
            streamed_text = ""
            history[turn_start:] = [progress_message("正在执行：" + tool_text(event.get("tool")))]

            tool_name = tool_text(
                event.get(
                    "tool"
                )
            )

            status_text = (
                f"正在执行："
                f"{tool_name}"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="执行中",
                tool=tool_name,
                detail=short_text(
                    event.get(
                        "arguments"
                    )
                ),
            )

            force_flush = True

        # ====================================================
        # Tool Completed
        # ====================================================

        elif (
            event_code
            == "tool_completed"
        ):

            tool_name = tool_text(
                event.get(
                    "tool"
                )
            )

            duration = event.get(
                "duration_ms",
                "",
            )

            detail = short_text(
                event.get(
                    "output",
                    "",
                )
            )

            if duration != "":

                detail = (
                    f"{duration} ms"
                    + (
                        f" · {detail}"
                        if detail
                        else ""
                    )
                )

            status_text = (
                "正在运行"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="成功",
                tool=tool_name,
                detail=detail,
            )

            force_flush = True

        # ====================================================
        # Tool Failed
        # ====================================================

        elif (
            event_code
            == "tool_failed"
        ):

            tool_name = tool_text(
                event.get(
                    "tool"
                )
            )

            status_text = (
                "工具执行失败，"
                "智能体正在尝试恢复…"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="失败",
                tool=tool_name,
                detail=short_text(
                    event.get(
                        "output",
                        "",
                    )
                ),
            )

            force_flush = True

        # ====================================================
        # Approval Approved
        # ====================================================

        elif (
            event_code
            == "approval_approved"
        ):

            tool_name = tool_text(
                event.get(
                    "tool"
                )
            )

            status_text = (
                "操作已批准，"
                "正在继续任务…"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="已批准",
                tool=tool_name,
            )

            force_flush = True

        # ====================================================
        # Approval Rejected
        # ====================================================

        elif (
            event_code
            == "approval_rejected"
        ):

            tool_name = tool_text(
                event.get(
                    "tool"
                )
            )

            status_text = (
                "操作已拒绝，"
                "正在继续任务…"
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="已拒绝",
                tool=tool_name,
            )

            force_flush = True

        # ====================================================
        # Approval Required
        # ====================================================

        if (
            terminal_status
            == "approval_required"
        ):

            tool_name = tool_text(
                event.get(
                    "tool"
                )
            )

            arguments = json_text(
                event.get(
                    "arguments"
                )
            )

            status_text = (
                "等待你的批准"
            )

            remaining = (
                event.get(
                    "remaining"
                )
                or 0
            )

            remaining_text = (
                f"\n\n还有 **{max(0, int(remaining) - 1)}** 个操作"
                "在排队等待审批。"
                if int(
                    remaining
                )
                > 1
                else ""
            )

            approval_text = approval_view(
                "### 需要人工批准\n\n"
                f"**操作：** {tool_name}\n\n"
                "**参数：**\n\n"
                "```text\n"
                f"{arguments}\n"
                "```"
                f"{remaining_text}",
                True,
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="等待审批",
                tool=tool_name,
                detail=short_text(
                    event.get(
                        "arguments"
                    )
                ),
            )

            force_flush = True

        # ====================================================
        # Completed
        # ====================================================

        elif (
            terminal_status
            == "completed"
        ):

            status_text = (
                "任务已完成"
            )

            final_message = (
                normalize_agent_text(
                    event.get(
                        "message",
                        "",
                    )
                )
            )

            final_message = final_message or streamed_text or "任务已完成，但未返回答案。"
            history[turn_start:] = [{"role": "assistant", "content": final_message}]

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="任务完成",
                detail=(
                    "任务 ID："
                    f"{event.get('task_id', '')}"
                ),
            )

            approval_text = (
                approval_view()
            )

            sources_text = (
                render_sources_markdown(
                    event.get(
                        "sources",
                        [],
                    )
                )
            )

            force_flush = True

        # ====================================================
        # Error
        # ====================================================

        elif (
            terminal_status
            == "error"
        ):

            status_text = (
                "任务执行失败"
            )

            error_text = str(
                event.get(
                    "message",
                    "未知错误",
                )
            )

            history.append(
                {
                    "role": "assistant",
                    "content": (
                        "任务执行出现错误：\n\n"
                        f"`{error_text}`"
                    ),
                }
            )

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="任务失败",
                detail=error_text,
            )

            approval_text = (
                approval_view()
            )

            force_flush = True

        # ====================================================
        # Cancelled：用户主动点了「停止」
        #
        # 这个分支必须显式存在。
        # 没有它的话，status_text 会永远停在"正在运行"，
        # 而下面的 task_finished 判定为假，
        # 连发送按钮都解不开——用户除了刷新页面
        # 再没有别的出路。
        # ====================================================

        elif (
            terminal_status
            == "cancelled"
        ):

            status_text = (
                "已停止当前任务"
            )

            cancel_text = str(
                event.get(
                    "message",
                    "已停止当前任务。",
                )
            )

            # 停止时很可能正卡在流式输出中间，
            # 把半截内容整理好；
            # 如果还一个字都没吐出来，就把空泡删掉。
            if (
                history
                and history[-1].get(
                    "role"
                )
                == "assistant"
            ):

                tail = (
                    normalize_agent_text(
                        history[-1].get(
                            "content",
                            "",
                        )
                    )
                )

                if tail.strip():

                    history[-1][
                        "content"
                    ] = tail

                else:

                    history.pop()

            trace_state = add_trace(
                trace_state,
                time_text=event.get(
                    "time",
                    "",
                ),
                status="已停止",
                detail=cancel_text,
            )

            approval_text = (
                approval_view()
            )

            force_flush = True

        if terminal_status or event_code == "task_cancelled":
            for message in history[turn_start:]:
                if message.get("metadata"):
                    message["metadata"] = {**message["metadata"], "status": "done", "title": status_text}

        # ====================================================
        # UI 节流
        # ====================================================

        current_time = (
            perf_counter()
        )

        if (
            not force_flush
            and (
                current_time
                - last_ui_flush
            )
            < flush_interval
        ):

            continue

        last_ui_flush = (
            current_time
        )

        waiting_approval = (
            terminal_status
            == "approval_required"
        )

        task_finished = (
            status_text
            in {
                "任务已完成",
                "任务执行失败",
                "已停止当前任务",
            }
        )

        # ====================================================
        # 停止按钮
        #
        # terminal_status 非空说明已到终态；
        # "task_cancelled" 不带 status 键，
        # 但它同样代表这个流即将结束，
        # 必须一并识别，否则后续帧会把按钮重新点亮。
        # ====================================================

        if (
            terminal_status
            or event_code
            == "task_cancelled"
        ):

            stop_enabled = False

        # 等待审批时不提供“停止”，
        # 此时该用的是审批卡里的“放弃”。
        can_stop = (
            stop_enabled
            and not waiting_approval
        )

        can_send = (
            task_finished
            and not waiting_approval
        )

        yield (
            history,
            status_text,
            service.get_session_id(),
            current_task_text(),
            approval_text,

            # 批准
            approval_button_state(
                waiting_approval
            ),

            # 拒绝
            approval_button_state(
                waiting_approval
            ),

            # 发送
            button_state(
                can_send
            ),

            # 新建会话
            button_state(
                can_send
            ),

            trace_state,
            trace_state,
            sources_text,

            # 停止
            button_state(
                can_stop
            ),
        )

# ============================================================
# 发送任务
# ============================================================

async def send_task(
    message,
    history,
    trace_state,
):

    message = (
        message or ""
    ).strip()

    if service.is_busy():
        gr.Warning("当前任务尚未结束，请先停止任务或处理待审批操作。")
        yield tuple(gr.skip() for _ in SEND_OUTPUTS)
        return

    # --------------------------------------------------------
    # 空输入
    # --------------------------------------------------------

    if not message:
        gr.Warning("请输入任务内容，或选择一个快捷任务。")

        yield (
            clone_history(
                history
            ),
            "请输入任务内容。",
            service.get_session_id(),
            current_task_text(),
            (
                approval_view()
            ),
            approval_button_state(False),
            approval_button_state(False),
            button_state(True),
            button_state(True),
            trace_state or [],
            trace_state or [],
            empty_sources_text(),
            button_state(False),
            "",
        )

        return

    # --------------------------------------------------------
    # 未配置模型：必须 yield 一次，
    # 否则界面完全不更新，看起来就是「点了发送没反应」。
    # --------------------------------------------------------

    from config import is_configured

    if not is_configured():

        gr.Warning(
            "还没有配置模型接口。"
            "请在右栏「设置 → 模型」填写接口地址、模型名称和 API Key。"
        )

        history = clone_history(
            history
        )

        history.append(
            {
                "role": "user",
                "content": message,
            }
        )

        history.append(
            {
                "role": "assistant",
                "content": (
                    "### 还不能开始任务\n\n"
                    "小智没有拿到可用的模型接口配置，"
                    "所以这次请求没有发出去。\n\n"
                    "请到**右栏「设置 → 模型」**填写：\n\n"
                    "1. 接口地址（例如 `https://…/v1`）\n"
                    "2. 模型名称\n"
                    "3. API Key\n\n"
                    "填完点「保存模型配置」，"
                    "可以直接用「测试连接」验证，"
                    "保存后**立即生效，不需要重启**。"
                ),
            }
        )

        yield (
            history,
            "未配置模型接口，请在「设置 → 模型」中配置。",
            service.get_session_id(),
            current_task_text(),
            approval_view(),
            approval_button_state(False),
            approval_button_state(False),
            button_state(True),
            button_state(True),
            trace_state or [],
            trace_state or [],
            empty_sources_text(),
            button_state(False),
            message,
        )

        return

    # --------------------------------------------------------
    # 用户消息立即显示
    # --------------------------------------------------------

    history = clone_history(
        history
    )

    history.append(
        {
            "role": "user",
            "content": message,
        }
    )

    history.append(progress_message())

    yield (
        history,
        "思考中 · 正在准备任务…",
        service.get_session_id(),
        current_task_text(),
        (
            approval_view()
        ),
        approval_button_state(False),
        approval_button_state(False),
        button_state(False),
        button_state(False),
        trace_state or [],
        trace_state or [],
        empty_sources_text(),
        button_state(True),
        "",
    )

    # --------------------------------------------------------
    # Streaming
    # --------------------------------------------------------

    async for update in (
        render_service_stream(
            service.stream_task(
                message
            ),
            history,
            trace_state,
        )
    ):

        yield (
            *update,
            gr.skip(),
        )

# ============================================================
# HITL 审批
# ============================================================

async def handle_approval(
    approved: bool,
    history,
    trace_state,
    remember: bool = False,
):

    async for update in (
        render_service_stream(
            service.stream_approval(
                approved=approved,
                remember=bool(
                    remember
                ),
            ),
            history,
            trace_state,
        )
    ):

        yield update

async def approve_task(
    history,
    trace_state,
    remember=False,
):

    async for update in (
        handle_approval(
            True,
            history,
            trace_state,
            remember=bool(
                remember
            ),
        )
    ):

        yield update

async def reject_task(
    history,
    trace_state,
):

    async for update in (
        handle_approval(
            False,
            history,
            trace_state,
        )
    ):

        yield update

# ============================================================
# Session
# ============================================================

def create_session():
    if service.is_busy():
        raise gr.Error("请先停止当前任务或处理待审批操作，再新建任务。")

    session_id = (
        service.new_session()
    )

    gr.Info("已创建新任务，可以开始新的对话。")
    return (
        # chatbot
        [],

        # session_box
        session_id,

        # task_box
        "—",

        # status_box
        "就绪",

        # approval_box
        approval_view(),

        # trace_table
        [],

        # trace_state
        [],

        # sources_box
        empty_sources_text(),

        # approve_button
        approval_button_state(False),

        # reject_button
        approval_button_state(False),

        # conversation_selector
        conversation_dropdown_state(
            session_id
        ),

        # conversation_title
        "新对话",

        # delete_confirm
        False,
    )

async def restore_view_ui():
    """Restore the current session and pending approval after a page refresh."""
    import asyncio
    while True:
        history = await service.get_current_chat_history()
        pending = list(service.pending_interruptions)
        running = service.running
        approval = approval_view()
        if pending:
            item = pending[0]
            approval = approval_view(
                "### 需要人工批准\n\n"
                f"**操作：** {tool_text(item.name)}\n\n"
                f"```text\n{json_text(item.arguments)}\n```",
                True,
            )
        status = "等待你的批准" if pending else ("任务运行中，可点击停止" if running else "就绪")
        yield (
            history, service.get_session_id(), current_task_text(),
            conversation_dropdown_state(), current_conversation_title(), approval,
            approval_button_state(bool(pending)), approval_button_state(bool(pending)),
            button_state(not (running or pending)), button_state(not (running or pending)),
            button_state(running and not pending), status, runtime_status_markdown(),
            task_plan_markdown(), context_badge_text(),
        )
        if not running:
            return
        await asyncio.sleep(.5)

async def switch_conversation_ui(
    session_id,
):
    """
    从左侧历史列表切换 Conversation。
    """

    if service.is_busy():
        gr.Warning("当前任务尚未结束，暂时不能切换会话。请先停止任务或处理审批。")
        updates = [gr.skip() for _ in SESSION_SWITCH_OUTPUTS]
        updates[8] = conversation_dropdown_state()
        return tuple(updates)

    if not session_id:

        history = (
            await service
            .get_current_chat_history()
        )

        return (
            history,
            service.get_session_id(),
            "—",
            "就绪",
            approval_view(),
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            approval_button_state(False),
            approval_button_state(False),
        )

    try:

        result = (
            await service
            .switch_conversation(
                session_id
            )
        )

        history = result.get(
            "history",
            [],
        )

        new_session_id = (
            result.get(
                "session_id"
            )
        )

        title = (
            current_conversation_title(
                new_session_id
            )
        )

        return (
            history,
            new_session_id,
            "—",
            "就绪",
            approval_view(),
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(
                new_session_id
            ),
            title,
            False,
            approval_button_state(False),
            approval_button_state(False),
        )

    except Exception as error:
        gr.Warning(f"切换会话失败：{error}")
        updates = [gr.skip() for _ in SESSION_SWITCH_OUTPUTS]
        updates[3] = f"切换会话失败：{error}"
        updates[8] = conversation_dropdown_state()
        return tuple(updates)

def rename_conversation_ui(
    title,
):
    """
    重命名当前 Conversation。
    """

    try:

        new_title = (
            service
            .rename_current_conversation(
                title
            )
        )

        return (
            conversation_dropdown_state(),
            new_title,
            "会话名称已更新",
        )

    except Exception as error:

        return (
            conversation_dropdown_state(),
            current_conversation_title(),
            (
                "重命名失败："
                f"{error}"
            ),
        )

async def delete_conversation_ui(
    confirmed,
):
    """
    删除当前 Conversation，
    并立即刷新整个左侧 Session 状态。
    """

    # =====================================
    # 没确认，不执行
    # =====================================

    if not confirmed:
        history = (
            await service
            .get_current_chat_history()
        )

        return (
            history,
            service.get_session_id(),
            "—",
            "请先勾选“确认删除当前对话”。",
            approval_view(),
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            approval_button_state(False),
            approval_button_state(False),
        )

    try:

        deleting_id = (
            service.get_session_id()
        )

        result = (
            await service
            .delete_conversation(
                deleting_id
            )
        )

        new_session_id = (
            result.get(
                "session_id"
            )
        )

        history = (
            result.get(
                "history",
                [],
            )
        )

        new_title = (
            current_conversation_title(
                new_session_id
            )
        )

        return (
            # chatbot
            history,

            # 会话 ID
            new_session_id,

            # 任务 ID
            "—",

            # Status
            "当前会话已删除，已创建新的会话。",

            # Approval
            approval_view(),

            # Trace table
            [],

            # Trace state
            [],

            # Sources
            empty_sources_text(),

            # Dropdown
            conversation_dropdown_state(
                new_session_id
            ),

            # Title
            new_title,

            # Delete confirm checkbox
            False,

            # Approve
            approval_button_state(False),

            # Reject
            approval_button_state(False),
        )

    except Exception as error:

        try:

            history = (
                await service
                .get_current_chat_history()
            )

        except Exception:

            history = []

        return (
            history,
            service.get_session_id(),
            "—",
            (
                "删除会话失败："
                f"{type(error).__name__}: "
                f"{error}"
            ),
            approval_view(),
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            approval_button_state(False),
            approval_button_state(False),
        )

def refresh_conversations_ui():

    return conversation_dropdown_state(
        service.get_session_id()
    )

def clear_chat():
    if service.is_busy():
        raise gr.Error("任务进行中，请先停止任务再清空显示。")
    gr.Info("已清空显示，历史记录仍保留。开始独立对话请点击“新任务”。")
    return (
        [],
        empty_sources_text(),
    )

# ============================================================
# Context Product UI
# ============================================================

def get_context_snapshot() -> dict:
    """
    读取当前会话的 Context v2.3 状态。
    所有字段均采用容错读取，避免未来 Context 结构扩展时 GUI 崩溃。
    """

    try:
        value = (
            service.get_context_status()
            or {}
        )

        if isinstance(
            value,
            dict,
        ):
            return value

    except Exception as error:

        return {
            "error": (
                f"{type(error).__name__}: "
                f"{error}"
            )
        }

    return {}

def context_badge_text() -> str:

    status = get_context_snapshot()

    if status.get("error"):
        return "**上下文 · 异常**"

    total_items = (
        status.get("total_items")
        or 0
    )

    has_summary = bool(
        status.get("has_summary")
    )

    label = (
        "摘要已启用"
        if has_summary
        else "原始上下文"
    )

    return (
        f"**上下文 · {label} · "
        f"{total_items} 项**"
    )

def context_markdown() -> str:

    status = get_context_snapshot()

    error = status.get("error")

    if error:
        return (
            "### 上下文\n\n"
            "读取当前上下文状态失败：\n\n"
            f"`{error}`"
        )

    total_items = (
        status.get("total_items")
        or 0
    )

    recent_items = (
        status.get("recent_context_items")
        or status.get("raw_context_items")
        or 0
    )

    recent_limit = (
        status.get("recent_context_limit")
        or 0
    )

    summarized_items = (
        status.get("summarized_items")
        or 0
    )

    summary_length = (
        status.get("summary_length")
        or 0
    )

    summary_trigger = (
        status.get("summary_trigger")
        or 0
    )

    has_summary = bool(
        status.get("has_summary")
    )

    updated_at = (
        status.get("summary_updated_at")
        or "—"
    )

    safe_raw_start = (
        status.get("safe_raw_start")
    )

    summary_state = (
        "已启用"
        if has_summary
        else "尚未生成"
    )

    lines = [
        "### 当前会话上下文",
        "",
        (
            f"**状态：** {summary_state}"
        ),
        "",
        (
            f"- 会话历史项：**{total_items}**"
        ),
        (
            f"- 当前原始上下文：**{recent_items}**"
            + (
                f" / 目标 {recent_limit}"
                if recent_limit
                else ""
            )
        ),
        (
            f"- 已摘要历史项：**{summarized_items}**"
        ),
        (
            f"- 摘要长度：**{summary_length} 字符**"
        ),
    ]

    if summary_trigger:
        lines.append(
            (
                f"- 自动整理阈值："
                f"**{summary_trigger} 项**"
            )
        )

    if safe_raw_start is not None:
        lines.append(
            (
                f"- 安全原始边界："
                f"**{safe_raw_start}**"
            )
        )

    lines.extend(
        [
            (
                f"- 摘要最近更新："
                f"**{updated_at}**"
            ),
            "",
            (
                "上下文会随当前对话"
                "持久化，并在长对话达到阈值后自动整理。"
            ),
        ]
    )

    # --------------------------------------------------
    # 用量
    #
    # 原先 set_tracing_disabled(True) 之后 Usage 直接丢弃，
    # 跑一次任务花了多少 token 完全靠猜。
    # --------------------------------------------------

    try:

        session_usage = (
            usage_stats.get_session_usage(
                service.get_session_id()
            )
        )

        total_usage = (
            usage_stats.get_total_usage()
        )

        lines.extend(
            [
                "",
                "### 用量（本会话）",
                "",
                usage_stats.format_usage(
                    session_usage
                ),
                "",
                "### 用量（全部会话累计）",
                "",
                usage_stats.format_usage(
                    total_usage
                ),
            ]
        )

    except Exception:

        # 用量展示失败不应该影响上下文面板
        pass

    return "\n".join(
        lines
    )

def refresh_context_ui():

    return (
        context_badge_text(),
        context_markdown(),
    )

# ============================================================
# 设置中心
# ============================================================

def _settings_snapshot() -> dict:

    return (
        read_settings()
        or {}
    )

def _model_settings() -> dict:

    return (
        _settings_snapshot()
        .get(
            "model",
            {},
        )
        or {}
    )

def _agent_settings() -> dict:

    return (
        _settings_snapshot()
        .get(
            "agent",
            {},
        )
        or {}
    )

def _web_settings() -> dict:

    return (
        _settings_snapshot()
        .get(
            "web",
            {},
        )
        or {}
    )

def _general_settings() -> dict:

    return (
        _settings_snapshot()
        .get(
            "general",
            {},
        )
        or {}
    )

def api_key_status_markdown() -> str:

    status = (
        model_api_key_status()
    )

    source = status.get(
        "source",
        "未配置",
    )

    masked = status.get(
        "masked",
        "未配置",
    )

    legacy = bool(
        status.get(
            "legacy_env_present"
        )
    )

    lines = [
        "### API 密钥状态",
        "",
        f"- 当前来源：**{source}**",
        f"- 当前密钥：`{masked}`",
    ]

    if legacy:
        lines.extend(
            [
                "",
                (
                    "> 检测到 `.env` 中仍存在旧密钥。"
                    "安全存储优先级更高；完成迁移验证后，"
                    "可再手动移除 `.env` 中的 `MODEL_API_KEY`。"
                ),
            ]
        )

    return "\n".join(
        lines
    )

def model_status_markdown() -> str:
    """
    左栏「模型与接口」卡片：一眼看出有没有配好。
    """

    import config

    name = str(
        getattr(
            config,
            "model_name",
            "",
        )
        or ""
    )

    url = str(
        getattr(
            config,
            "base_url",
            "",
        )
        or ""
    )

    ready = bool(
        config.is_configured()
    )

    if ready:

        short_url = url

        if len(short_url) > 34:
            short_url = (
                short_url[:31]
                + "…"
            )

        return (
            '<div class="side-path">'
            '<span class="side-path-name">'
            f'{html.escape(name)}'
            "</span>"
            '<span class="side-path-count">已配置</span>'
            "</div>"
            '<div class="side-path-full" '
            f'title="{html.escape(url)}">'
            f"{html.escape(short_url)}"
            "</div>"
        )

    return (
        '<div class="side-path">'
        '<span class="side-path-name warn">'
        "未配置接口"
        "</span>"
        '<span class="side-path-count">'
        "不可用"
        "</span>"
        "</div>"
        '<div class="side-path-full">'
        "填接口地址、模型名称和 API Key 后才能对话"
        "</div>"
    )


def _valid_http_url(
    value: str,
) -> bool:

    try:

        parsed = urlsplit(
            (
                value
                or ""
            ).strip()
        )

        return (
            parsed.scheme
            in {
                "http",
                "https",
            }
            and bool(
                parsed.netloc
            )
        )

    except Exception:
        return False

def save_general_settings_ui(
    open_browser,
):

    update_settings(
        {
            "general": {
                "language":
                    "zh-CN",
                "open_browser":
                    bool(open_browser),
            }
        }
    )

    return (
        "常规设置已保存。"
        "“启动时自动打开浏览器”将在下次启动时生效。"
    )

def save_model_settings_ui(
    provider_name,
    base_url,
    model_name,
    api_key_input,
):

    provider_name = (
        provider_name
        or "OpenAI 兼容接口"
    ).strip()

    base_url = (
        base_url
        or ""
    ).strip()

    model_name = (
        model_name
        or ""
    ).strip()

    api_key_input = (
        api_key_input
        or ""
    ).strip()

    if not _valid_http_url(
        base_url
    ):
        return (
            "保存失败：接口地址必须是有效的 "
            "`http://` 或 `https://` URL。",
            api_key_status_markdown(),
            "",
            model_status_markdown(),
        )

    if not model_name:

        return (
            "保存失败：模型名称不能为空。",
            api_key_status_markdown(),
            "",
            model_status_markdown(),
        )

    update_settings(
        {
            "model": {
                "provider_name":
                    provider_name,
                "base_url":
                    base_url,
                "model_name":
                    model_name,
            }
        }
    )

    if api_key_input:

        try:

            set_model_api_key(
                api_key_input
            )

        except Exception as error:

            return (
                (
                    "模型配置已保存，但 API 密钥保存失败："
                    f"{type(error).__name__}: {error}"
                ),
                api_key_status_markdown(),
                "",
                model_status_markdown(),
            )

    # --------------------------------------------------------
    # 热生效：重建 client / model 并挂到已创建的 Agent 上，
    # 这样保存后立刻可用，不需要重启。
    # --------------------------------------------------------

    import config

    try:

        applied = (
            config.reload_runtime()
        )

        note = (
            "模型配置已保存并**立即生效**，"
            "不需要重启。\n\n"
            f"- 模型：`{applied.get('model_name', '')}`\n"
            f"- 接口：`{applied.get('base_url', '')}`\n\n"
            "建议再点一次「测试连接」确认可用。"
        )

    except Exception as error:

        note = (
            "模型配置已保存，但热更新失败："
            f"{type(error).__name__}: {error}\n\n"
            "请重启小智后生效。"
        )

    return (
        note,
        api_key_status_markdown(),
        "",
        model_status_markdown(),
    )

def delete_api_key_ui():

    try:

        delete_model_api_key()

        return (
            (
                "已删除 Windows 安全存储中的 API Key。"
                "如果 `.env` 中仍有 `MODEL_API_KEY`，"
                "重启后仍会使用 `.env` 作为回退。"
            ),
            api_key_status_markdown(),
        )

    except Exception as error:

        return (
            (
                "删除失败："
                f"{type(error).__name__}: "
                f"{error}"
            ),
            api_key_status_markdown(),
        )

def migrate_api_key_ui():

    try:

        result = (
            migrate_env_key_to_keyring()
        )

        return (
            result.get(
                "message",
                "",
            ),
            api_key_status_markdown(),
        )

    except Exception as error:

        return (
            (
                "迁移失败："
                f"{type(error).__name__}: "
                f"{error}"
            ),
            api_key_status_markdown(),
        )

def test_model_connection_ui(
    base_url,
    model_name,
    api_key_input,
):

    base_url = (
        base_url
        or ""
    ).strip()

    model_name = (
        model_name
        or ""
    ).strip()

    api_key = (
        (
            api_key_input
            or ""
        ).strip()
        or get_model_api_key()
    )

    if not _valid_http_url(
        base_url
    ):
        return (
            "测试失败：接口地址不是有效 URL。"
        )

    if not api_key:

        return (
            "测试失败：没有可用 API Key。"
        )

    models_url = (
        base_url.rstrip("/")
        + "/models"
    )

    try:

        response = httpx.get(
            models_url,
            headers={
                "Authorization":
                    f"Bearer {api_key}",
            },
            timeout=10,
        )

        if response.status_code in {
            401,
            403,
        }:

            return (
                "连接失败：服务返回鉴权错误 "
                f"HTTP {response.status_code}。"
            )

        response.raise_for_status()

        model_ids = []

        try:

            payload = response.json()

            data = payload.get(
                "data",
                []
            )

            if isinstance(
                data,
                list,
            ):

                model_ids = [
                    str(
                        item.get(
                            "id",
                            "",
                        )
                    )
                    for item in data
                    if isinstance(
                        item,
                        dict,
                    )
                ]

        except Exception:
            pass

        if (
            model_name
            and model_ids
            and model_name
            in model_ids
        ):

            return (
                "连接成功，且模型列表中找到了 "
                f"`{model_name}`。"
            )

        if (
            model_name
            and model_ids
        ):

            return (
                "接口连接成功，但 `/models` 返回的模型列表中"
                f"没有找到 `{model_name}`。"
                "部分兼容服务可能使用别名，"
                "可继续以实际调用结果为准。"
            )

        return (
            "接口连接成功。"
            "服务未返回可解析的模型列表，"
            "但鉴权与 `/models` 请求已通过。"
        )

    except httpx.HTTPStatusError as error:

        return (
            "连接失败："
            f"HTTP {error.response.status_code}。"
            "部分 OpenAI 兼容服务不实现 `/models`，"
            "如果聊天本身正常，可以忽略这一项。"
        )

    except Exception as error:

        return (
            "连接失败："
            f"{type(error).__name__}: "
            f"{error}"
        )

def save_agent_settings_ui(
    max_turns,
):

    try:

        max_turns = int(
            max_turns
        )

    except Exception:
        max_turns = 10

    max_turns = max(
        3,
        min(
            40,
            max_turns,
        ),
    )

    update_settings(
        {
            "agent": {
                "max_turns":
                    max_turns,
            }
        }
    )

    return (
        "智能体设置已保存。"
        f"后续新任务最大轮数：{max_turns}。"
    )

def save_web_settings_ui(
    normal_budget,
    research_budget,
    fetch_timeout,
):

    normal_budget = int(
        normal_budget
    )

    research_budget = int(
        research_budget
    )

    fetch_timeout = int(
        fetch_timeout
    )

    normal_budget = max(
        1,
        min(
            12,
            normal_budget,
        ),
    )

    research_budget = max(
        normal_budget,
        min(
            20,
            research_budget,
        ),
    )

    fetch_timeout = max(
        5,
        min(
            60,
            fetch_timeout,
        ),
    )

    update_settings(
        {
            "web": {
                "normal_search_budget":
                    normal_budget,
                "research_search_budget":
                    research_budget,
                "fetch_timeout_seconds":
                    fetch_timeout,
            }
        }
    )

    return (
        "联网设置已保存并会应用到后续任务。"
        f"普通搜索预算 {normal_budget} 次，"
        f"深度研究预算 {research_budget} 次，"
        f"网页读取超时 {fetch_timeout} 秒。"
    )

def settings_info_markdown() -> str:

    return (
        "### 配置文件\n\n"
        f"`{SETTINGS_PATH}`\n\n"
        "该文件只保存**非敏感设置**。"
        "API Key 不会写入这里，"
        "而是优先保存到 Windows 安全凭据存储。"
    )

# ============================================================
# MCP 管理器 UI
# ============================================================

MCP_TABLE_HEADERS = [
    "名称",
    "传输",
    "状态",
    "批准策略",
    "安全配置",
    "目标",
]

def mcp_dependency_markdown() -> str:

    status = (
        mcp_dependency_status()
    )

    agents_version = (
        status.get(
            "openai_agents",
            "未知",
        )
    )

    mcp_version = (
        status.get(
            "mcp",
            "未安装",
        )
    )

    ready = bool(
        status.get(
            "ready"
        )
    )

    state = (
        "可用"
        if ready
        else "尚未就绪"
    )

    return (
        "### MCP 运行环境\n\n"
        f"- OpenAI Agents SDK：`{agents_version}`\n"
        f"- MCP Python SDK：`{mcp_version}`\n"
        f"- 状态：**{state}**"
        + (
            ""
            if ready
            else (
                "\n\n请安装："
                '`pip install "mcp>=1.19.0,<3"`'
            )
        )
    )

def mcp_selector_state(
    selected=None,
):

    choices = (
        mcp_server_choices()
    )

    values = [
        value
        for _, value
        in choices
    ]

    value = (
        selected
        if selected in values
        else (
            values[0]
            if values
            else None
        )
    )

    return gr.Dropdown(
        choices=choices,
        value=value,
    )

def mcp_empty_form():

    return (
        "",
        "stdio",
        False,
        "smart",
        "",
        True,
        10,
        "",
        "",
        "",
        "{}",
        "",
        "{}",
        "",
        "",
        "新建 MCP 配置。默认停用，默认采用智能审批策略。",
    )

def mcp_load_ui(
    server_id,
):

    server = (
        get_mcp_server(
            server_id
        )
    )

    if not server:

        return mcp_empty_form()

    stdio = (
        server.get(
            "stdio",
            {},
        )
        or {}
    )

    http = (
        server.get(
            "http",
            {},
        )
        or {}
    )

    normal_env = (
        stdio.get(
            "env",
            {},
        )
        or {}
    )

    normal_headers = (
        http.get(
            "headers",
            {},
        )
        or {}
    )

    secret_messages = []

    if server.get(
        "has_secret_headers"
    ):

        secret_messages.append(
            "已保存安全请求头"
        )

    if server.get(
        "has_secret_env"
    ):

        secret_messages.append(
            "已保存安全环境变量"
        )

    secret_text = (
        "；".join(
            secret_messages
        )
        if secret_messages
        else "未保存安全字段"
    )

    return (
        server.get(
            "name",
            "",
        ),
        server.get(
            "transport",
            "stdio",
        ),
        bool(
            server.get(
                "enabled",
                False,
            )
        ),
        server.get(
            "require_approval",
            "smart",
        ),
        server.get(
            "tool_prefix",
            "",
        ),
        bool(
            server.get(
                "cache_tools_list",
                True,
            )
        ),
        int(
            server.get(
                "timeout_seconds",
                10,
            )
        ),
        stdio.get(
            "command",
            "",
        ),
        format_args_text(
            stdio.get(
                "args",
                [],
            )
        ),
        stdio.get(
            "cwd",
            "",
        ),
        json.dumps(
            normal_env,
            ensure_ascii=False,
            indent=2,
        ),
        "",
        http.get(
            "url",
            "",
        ),
        json.dumps(
            normal_headers,
            ensure_ascii=False,
            indent=2,
        ),
        "",
        (
            f"已加载：**{server.get('name', '')}**。"
            f"{secret_text}。"
            "安全字段不会回显；留空保存表示保持原值。"
        ),
    )

def mcp_save_ui(
    server_id,
    name,
    transport,
    enabled,
    require_approval,
    tool_prefix,
    cache_tools_list,
    timeout_seconds,
    stdio_command,
    stdio_args_text,
    stdio_cwd,
    stdio_env_json,
    secret_env_json,
    http_url,
    http_headers_json,
    secret_headers_json,
):

    server_id = (
        server_id
        or ""
    ).strip()

    name = (
        name
        or ""
    ).strip()

    if not name:

        return (
            mcp_selector_state(
                server_id
            ),
            mcp_server_rows(),
            server_id,
            "保存失败：名称不能为空。",
            "",
            "",
        )

    try:

        normal_env = (
            parse_json_object_text(
                stdio_env_json,
                field_name=(
                    "普通环境变量"
                ),
            )
        )

        normal_headers = (
            parse_json_object_text(
                http_headers_json,
                field_name=(
                    "普通请求头"
                ),
            )
        )

        replace_secret_env = bool(
            (
                secret_env_json
                or ""
            ).strip()
        )

        replace_secret_headers = bool(
            (
                secret_headers_json
                or ""
            ).strip()
        )

        secret_env = (
            parse_json_object_text(
                secret_env_json,
                field_name=(
                    "安全环境变量"
                ),
            )
            if replace_secret_env
            else None
        )

        secret_headers = (
            parse_json_object_text(
                secret_headers_json,
                field_name=(
                    "安全请求头"
                ),
            )
            if replace_secret_headers
            else None
        )

        payload = {
            "id": server_id,
            "name": name,
            "tool_prefix":
                (
                    tool_prefix
                    or ""
                ).strip(),
            "transport":
                transport,
            "enabled":
                bool(enabled),
            "require_approval":
                require_approval,
            "cache_tools_list":
                bool(
                    cache_tools_list
                ),
            "timeout_seconds":
                int(
                    timeout_seconds
                ),
            "stdio": {
                "command":
                    (
                        stdio_command
                        or ""
                    ).strip(),
                "args":
                    parse_args_text(
                        stdio_args_text
                    ),
                "cwd":
                    (
                        stdio_cwd
                        or ""
                    ).strip(),
                "env":
                    normal_env,
            },
            "http": {
                "url":
                    (
                        http_url
                        or ""
                    ).strip(),
                "headers":
                    normal_headers,
            },
        }

        saved = (
            save_mcp_server(
                payload,
                secret_headers=
                    secret_headers,
                secret_env=
                    secret_env,
                replace_secret_headers=
                    replace_secret_headers,
                replace_secret_env=
                    replace_secret_env,
            )
        )

        saved_id = (
            saved.get(
                "id",
                "",
            )
        )

        return (
            mcp_selector_state(
                saved_id
            ),
            mcp_server_rows(),
            saved_id,
            (
                "配置已保存。"
                "启用状态只表示允许后续 Agent 接入；"
                "当前 v1.6 第一阶段不会自动连接。"
            ),
            "",
            "",
        )

    except Exception as error:

        return (
            mcp_selector_state(
                server_id
            ),
            mcp_server_rows(),
            server_id,
            (
                "保存失败："
                f"{type(error).__name__}: "
                f"{error}"
            ),
            secret_env_json,
            secret_headers_json,
        )

async def warmup_mcp_ui():
    """
    页面加载后后台预热 MCP 连接池。
    """

    try:

        await service.warmup_mcp_pool()

    except Exception:
        pass

    return None


async def reset_mcp_pool_ui():
    """
    MCP 配置变更后销毁连接池，下次任务按新配置重连。
    """

    try:

        await service.reset_mcp_pool()

    except Exception:
        pass

    return None


def mcp_delete_ui(
    server_id,
):

    if not server_id:

        return (
            mcp_selector_state(),
            mcp_server_rows(),
            "",
            "没有选择要删除的 MCP 配置。",
        )

    deleted = (
        delete_mcp_server(
            server_id
        )
    )

    choices = (
        mcp_server_choices()
    )

    next_id = (
        choices[0][1]
        if choices
        else ""
    )

    return (
        mcp_selector_state(
            next_id
        ),
        mcp_server_rows(),
        next_id,
        (
            "MCP 配置及其安全字段已删除。"
            if deleted
            else "没有找到该 MCP 配置。"
        ),
    )

async def mcp_test_ui(
    server_id,
):

    if not server_id:

        return (
            "请先选择并保存一个 MCP 配置。",
            [],
        )

    result = await (
        test_mcp_server(
            server_id
        )
    )

    tools = [
        [
            item.get(
                "name",
                "",
            ),
            item.get(
                "description",
                "",
            ),
        ]
        for item in (
            result.get(
                "tools",
                [],
            )
            or []
        )
    ]

    if result.get(
        "ok"
    ):

        return (
            (
                "### 连接成功\n\n"
                f"服务器：**{result.get('server_name', '')}**\n\n"
                f"发现工具：**{result.get('tool_count', 0)} 个**"
            ),
            tools,
        )

    return (
        (
            "### 连接失败\n\n"
            f"`{result.get('error', '未知错误')}`"
        ),
        [],
    )

def mcp_refresh_ui():

    choices = (
        mcp_server_choices()
    )

    selected = (
        choices[0][1]
        if choices
        else None
    )

    return (
        mcp_selector_state(
            selected
        ),
        mcp_server_rows(),
        mcp_dependency_markdown(),
    )

# ============================================================
# 快速任务（新手引导）
# ============================================================

QUICK_PROMPTS = [
    (
        "搜索最新 AI 新闻",
        "搜索今天人工智能领域的重要新闻，"
        "给出要点总结并标注来源 [S1] [S2]。",
    ),
    (
        "查看 GitHub 热门",
        "查看当前 GitHub 每日热门项目，"
        "列出前 5 个并说明它们做什么。",
    ),
    (
        "用 MCP 浏览工作区",
        "只使用 MCP 工具列出工作区文件，"
        "然后告诉我里面有什么。",
    ),
    (
        "整理工作区文件",
        "读取工作区里的文件内容，"
        "帮我做一份结构化整理摘要。",
    ),
    (
        "读取指定网页",
        "读取 https://openai.github.io/"
        "openai-agents-python/ 并总结要点。",
    ),
    (
        "记一条笔记",
        "帮我保存一条笔记："
        "今天完成了 MCP Runtime 接入。",
    ),
]

# ============================================================
# 空状态（Welcome）
# ============================================================

WELCOME_CARDS = [
    (
        "搜索今天的 AI 新闻",
        "联网检索，要点总结并标注来源",
        "搜索今天人工智能领域的重要新闻，"
        "给出要点总结并标注来源 [S1] [S2]。",
    ),
    (
        "查看 GitHub 热门",
        "抓取官方 Trending 日榜",
        "查看当前 GitHub 每日热门项目，"
        "列出前 5 个并说明它们做什么。",
    ),
    (
        "用 MCP 浏览工作区",
        "走 MCP 文件系统工具列目录",
        "只使用 MCP 工具列出工作区文件，"
        "然后告诉我里面有什么。",
    ),
    (
        "整理工作区文件",
        "读取内容，输出结构化摘要",
        "读取工作区里的文件内容，"
        "帮我做一份结构化整理摘要。",
    ),
    (
        "读取并总结网页",
        "抓取正文，提炼关键要点",
        "读取 https://openai.github.io/"
        "openai-agents-python/ 并总结要点。",
    ),
    (
        "记一条笔记",
        "写入本地笔记库",
        "帮我保存一条笔记："
        "今天完成了 MCP Runtime 接入。",
    ),
]

def welcome_html() -> str:
    """
    对话为空时的欢迎区。

    卡片点击由 KEYBOARD_JS 统一处理：
    把 data-prompt 写回输入框。
    """

    cards = []

    for title, desc, prompt in (
        WELCOME_CARDS[:4]
    ):

        cards.append(
            '<button type="button" '
            'class="welcome-card" '
            'data-prompt="%s">'
            "<b>%s</b>"
            "<span>%s</span>"
            "</button>"
            % (
                html.escape(
                    prompt,
                    quote=True,
                ),
                html.escape(title),
                html.escape(desc),
            )
        )

    return (
        '<div class="welcome-inner">'
        '<div class="welcome-mark"></div>'
        '<h1 class="welcome-title">'
        "今天，想完成什么？"
        "</h1>"
        '<p class="welcome-sub">'
        "从研究、文件到代码，把目标交给我。"
        "选择一个起点，或直接描述你的任务。"
        "</p>"
        '<div class="welcome-grid">'
        "%s"
        "</div>"
        "</div>"
        % "".join(cards)
    )

# ============================================================
# 运行时状态
# ============================================================

def _current_model_name() -> str:

    try:

        from config import (
            model_name,
        )

        return str(
            model_name
            or ""
        ).strip()

    except Exception:

        return ""

def runtime_status_markdown() -> str:
    """
    顶栏运行时状态徽章。

    让用户随时知道：
        当前模型
        MCP 启用数量
        任务状态
    """

    model = (
        _current_model_name()
        or "未配置"
    )

    try:

        enabled_count = sum(
            1
            for item in list_mcp_servers()
            if item.get(
                "enabled",
                False,
            )
        )

    except Exception:

        enabled_count = 0

    if (
        service.pending_interruptions
    ):

        state = "等待审批"
        dot = "wait"

    elif service.running:

        state = "执行中"
        dot = "run"

    else:

        state = "就绪"
        dot = "idle"

    mcp_text = (
        f"{enabled_count} 个已启用"
        if enabled_count
        else "未启用"
    )

    if dot == "wait":
        state_cls = "warn"
        dot_cls = "busy"
    elif dot == "run":
        state_cls = "ok"
        dot_cls = "busy"
    else:
        state_cls = "ok"
        dot_cls = "ok"

    return (
        f'<span class="chip {state_cls}">'
        f'<span class="dot {dot_cls}"></span>'
        f"{state}</span>"
        f'<span class="chip">{html.escape(model)}</span>'
        f'<span class="chip mcp">'
        f"MCP {mcp_text}</span>"
    )

# ============================================================
# 工具总览
# ============================================================

def tools_overview_markdown() -> str:
    """
    展示 Agent 当前可用的全部工具。

    目的：
    让用户一眼看懂「小智能做什么」。
    """

    lines = []

    lines.append(
        "### 内置工具"
    )
    lines.append("")

    builtin = [
        ("update_plan", "多步骤任务计划与进度"),
        ("search_workspace", "代码与文本搜索"),
        ("read_file_lines", "按行读取代码"),
        ("edit_file", "精确编辑文件（需批准）"),
        ("run_shell", "本地 PowerShell / 测试 / Git（需批准，最长 120 秒）"),
        ("list_mcp_content", "MCP 资源与提示列表"),
        ("read_mcp_resource", "读取 MCP 资源（需批准）"),
        ("get_mcp_prompt", "获取 MCP 模板（需批准）"),
        (
            "calculator",
            "计算表达式",
        ),
        (
            "get_current_time",
            "获取当前时间",
        ),
        (
            "save_note",
            "保存笔记",
        ),
        (
            "read_notes",
            "读取笔记",
        ),
        (
            "list_files",
            "列出工作区文件",
        ),
        (
            "read_file",
            "读取工作区文件",
        ),
        (
            "write_file",
            "写入工作区文件（需批准）",
        ),
        (
            "web_search",
            "联网搜索",
        ),
        (
            "web_fetch",
            "读取网页正文",
        ),
        (
            "github_trending",
            "GitHub 官方热门榜",
        ),
        (
            "finish_task",
            "结束任务并给出结论",
        ),
    ]

    for name, desc in builtin:

        lines.append(
            f"- `{name}` — {desc}"
        )

    lines.append("")
    lines.append(
        "### MCP 工具"
    )
    lines.append("")

    try:

        servers = [
            item
            for item in list_mcp_servers()
            if item.get(
                "enabled",
                False,
            )
        ]

    except Exception:

        servers = []

    if not servers:

        lines.append(
            "当前没有已启用的 MCP 服务器。"
        )
        lines.append("")
        lines.append(
            "可以在「设置 → MCP」中启用。"
        )

    else:

        for item in servers:

            prefix = (
                item.get(
                    "tool_prefix",
                    "",
                )
                or "mcp"
            )

            approval = (
                item.get(
                    "require_approval",
                    "smart",
                )
            )

            approval_text = {
                "smart":
                    "只读自动 / 写入询问",
                "always":
                    "每次询问",
                "never":
                    "自动执行",
            }.get(
                approval,
                approval,
            )

            lines.append(
                f"**{item.get('name', '未命名')}**"
            )
            lines.append("")
            lines.append(
                f"- 工具名前缀：`mcp_{prefix}__`"
            )
            lines.append(
                f"- 传输："
                f"{item.get('transport', '')}"
            )
            lines.append(
                f"- 审批：{approval_text}"
            )
            lines.append("")

    return "\n".join(
        lines
    )

# ============================================================
# 停止任务
# ============================================================

def stop_task():
    """
    请求停止当前正在运行的任务。

    点完立刻把按钮自己置灰，
    一是避免重复点击，
    二是给即时反馈——真正停下还需要一两秒。

    空闲时不会被误触发，
    但也不该假装"正在停止"，
    所以这里如实说明。
    """

    if not service.running:

        return (
            "当前没有正在运行的任务。",
            button_state(False),
        )

    service.request_cancel()

    return (
        "正在停止…",
        button_state(False),
    )

# ============================================================
# 导出对话
# ============================================================

def export_conversation_markdown() -> tuple:
    """
    把当前会话导出为 Markdown 文件。
    """

    import asyncio

    try:

        history = asyncio.run(
            service.get_current_chat_history()
        )

    except Exception:

        history = []

    if not history:

        return (
            gr.update(
                value=None,
                visible=False,
            ),
            "当前会话没有可导出的内容。",
        )

    lines = []

    lines.append(
        "# 对话记录"
    )
    lines.append("")
    lines.append(
        f"- 会话 ID："
        f"`{service.get_session_id()}`"
    )
    lines.append(
        f"- 模型："
        f"`{_current_model_name() or '未配置'}`"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    for item in history:

        role = (
            item.get(
                "role"
            )
            if isinstance(
                item,
                dict,
            )
            else None
        )

        content = (
            item.get(
                "content"
            )
            if isinstance(
                item,
                dict,
            )
            else item
        )

        if isinstance(
            content,
            list,
        ):

            content = (
                "\n".join(
                    str(
                        part
                    )
                    for part in content
                )
            )

        content = str(
            content
            or ""
        )

        if role == "user":

            lines.append(
                "## 你"
            )

        elif role == "assistant":

            lines.append(
                "## 小智"
            )

        else:

            continue

        lines.append("")
        lines.append(
            content
        )
        lines.append("")

    # 导出文件放在 data/exports，
    # 不要把 Agent 的工作区污染成输出目录。
    export_dir = (
        PROJECT_ROOT
        / "data"
        / "exports"
    )

    export_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    file_path = (
        export_dir
        / f"对话导出-{stamp}.md"
    )

    file_path.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )

    return (
        gr.update(
            value=str(file_path),
            visible=True,
        ),
        f"已导出：{file_path.name}",
    )

# ============================================================
# CSS
# ============================================================

CSS = r"""
/* ==========================================================
   Design System v2
   深色 / hairline 边框 / 分层面板 / 单一强调色
   ========================================================== */

:root {
    /* 最底层保持实色：玻璃要"糊"的是它上面的彩色光晕 */
    --bg: #07070b;
    --bg-elev: rgba(255, 255, 255, .028);

    /* ---- 玻璃色板 ----
       底色越薄越通透，厚度感来自描边和内高光，不是靠堆不透明度。 */
    --glass: rgba(255, 255, 255, .055);
    --glass-2: rgba(255, 255, 255, .085);
    --glass-3: rgba(255, 255, 255, .12);
    /* 代码/表格这类需要压暗保证可读性的地方 */
    --glass-deep: rgba(8, 10, 16, .58);

    /* 玻璃描边：上缘受光、下缘落影，才有厚度 */
    --glass-bd: rgba(255, 255, 255, .10);
    --glass-bd-2: rgba(255, 255, 255, .17);
    --glass-hi: rgba(255, 255, 255, .15);
    --glass-lo: rgba(0, 0, 0, .20);

    --blur-sm: 12px;
    --blur: 24px;
    --blur-lg: 38px;
    --sat: 175%;

    /* 兼容旧引用的面层色（现已全部半透明） */
    --panel: rgba(255, 255, 255, .055);
    --panel-2: rgba(255, 255, 255, .085);
    --panel-3: rgba(255, 255, 255, .12);
    --hover: rgba(255, 255, 255, .09);

    /* hairline 边框：用白色低透明度，避免脏灰 */
    --hair: rgba(255, 255, 255, .075);
    --bd: rgba(255, 255, 255, .10);
    --bd-2: rgba(255, 255, 255, .16);
    --bd-3: rgba(255, 255, 255, .24);

    /* 文字 */
    --tx: #f2f2f7;
    --tx-2: #b8b8c6;
    --muted: #8a8a99;
    --faint: #6a6a7c;

    /* 强调色（Codex 绿） */
    --accent: #10a37f;
    --accent-hi: #17bd93;
    --accent-lo: #0b7d61;
    --accent-soft: rgba(16, 163, 127, .13);
    --accent-bd: rgba(16, 163, 127, .40);

    /* 语义色 */
    --ok: #46c76e;
    --warn: #d9a13b;
    --err: #ef6a78;
    --mcp: #a97bf5;

    /* 字体 */
    --sans: "Plus Jakarta Sans", "PingFang SC",
            "Microsoft YaHei UI", "Segoe UI Variable Text",
            "Segoe UI", -apple-system, sans-serif;
    --mono: "JetBrains Mono", "Cascadia Code",
            "SFMono-Regular", Menlo, Consolas, monospace;

    /* 圆角：外大内小 */
    --r-xs: 6px;
    --r-sm: 8px;
    --r-md: 12px;
    --r-lg: 16px;
    --r-xl: 20px;

    /* 阴影：玻璃落影要散而柔，硬边投影会立刻显廉价 */
    --sh-sm: 0 2px 8px rgba(0, 0, 0, .26);
    --sh-md: 0 10px 30px rgba(0, 0, 0, .34);
    --sh-lg: 0 26px 70px rgba(0, 0, 0, .46);

    /* 玻璃边缘：上缘受光 + 下缘落影，这一条是"厚度"的来源 */
    --gl-edge:
        inset 0 1px 0 var(--glass-hi),
        inset 0 -1px 0 var(--glass-lo);

    /* 节律 */
    --sp-1: 4px;
    --sp-2: 8px;
    --sp-3: 12px;
    --sp-4: 16px;
    --sp-5: 24px;
    --sp-6: 32px;
}

/* ==========================================================
   Base / 背景层次
   ========================================================== */

html, body {
    background: var(--bg) !important;
    color: var(--tx);
    height: 100%;
}

body {
    font-family: var(--sans) !important;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
}

.gradio-container {
    max-width: 100% !important;
    width: 100% !important;
    height: 100vh !important;
    margin: 0 !important;
    padding: 0 !important;
    /* 基底光晕铺在这里，而不是 body 伪元素上——
       只有这一层不会被自身的不透明底色盖住。
       四角一团冷/暖色 + 中央一层极淡靛蓝，
       三栏的玻璃才有稳定的色彩可透。 */
    background:
        radial-gradient(
            46% 50% at 16% 20%,
            rgba(16, 163, 127, .24),
            transparent 70%
        ),
        radial-gradient(
            44% 48% at 88% 14%,
            rgba(169, 123, 245, .22),
            transparent 70%
        ),
        radial-gradient(
            50% 54% at 64% 92%,
            rgba(46, 132, 255, .20),
            transparent 72%
        ),
        radial-gradient(
            40% 44% at 4% 88%,
            rgba(255, 138, 96, .13),
            transparent 72%
        ),
        radial-gradient(
            62% 46% at 50% 58%,
            rgba(140, 160, 255, .06),
            transparent 74%
        ),
        #07070b !important;
    border: none !important;
    border-radius: 0 !important;
    overflow: hidden !important;
    position: relative;
}

/* Gradio 6 的中间层默认带 16px/32px 内边距和外边距，
   会把整个应用往下、往右推，必须清零。 */
.gradio-container > .main,
.gradio-container .main.app,
.gradio-container .main > .wrap,
.gradio-container main.contain {
    padding: 0 !important;
    margin: 0 !important;
    gap: 0 !important;
    max-width: 100% !important;
    background: transparent !important;
    overflow: hidden !important;
}

.gradio-container main.contain {
    flex-direction: column !important;
}

.gradio-container main.contain > .column {
    gap: 0 !important;
    background: transparent !important;
    display: flex !important;
    flex-direction: column !important;
    height: 100% !important;
    max-height: 100% !important;
    min-height: 0 !important;
    overflow: hidden !important;
}

/* ==========================================================
   背景光晕层
   毛玻璃必须有"东西可糊"。
   底层铺几团大面积柔光，玻璃面板才能透出色彩层次；
   否则 backdrop-filter 糊的是一片纯色，等于白加。
   ========================================================== */

/* 注意：光晕不能放在 body::before/::after 上。
   .gradio-container 自带不透明底色，会把它整个盖住（只露出边缘 8px）。
   基底光晕必须铺进 .gradio-container 自己的 background（见下方），
   这里只保留一层会漂移的动态光斑。 */

/* 漂移光斑：在画面中部缓慢游走，
   让中栏留白也有活的色彩，不至于死黑。
   范围小、周期长，只求"察觉不到但确实在动"。 */
.gradio-container::after {
    content: "";
    position: fixed;
    inset: -15%;
    pointer-events: none;
    z-index: 0;
    background:
        radial-gradient(
            24% 28% at 30% 32%,
            rgba(16, 163, 127, .22),
            transparent 70%
        ),
        radial-gradient(
            26% 30% at 72% 68%,
            rgba(46, 132, 255, .18),
            transparent 72%
        );
    filter: blur(42px);
    animation: drift-a 34s ease-in-out infinite alternate;
}

@keyframes drift-a {
    from { transform: translate3d(-2%, -1.5%, 0) scale(1); }
    to   { transform: translate3d(3%, 2.5%, 0) scale(1.08); }
}
@keyframes drift-b {
    from { transform: translate3d(2.5%, 2%, 0) scale(1.06); }
    to   { transform: translate3d(-3%, -2%, 0) scale(1); }
}

/* 尊重系统的"减少动态效果"设置 */
@media (prefers-reduced-motion: reduce) {
    .gradio-container::after { animation: none; }
}

/* 极细颗粒，去掉纯色平面的廉价感 */
.gradio-container::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 1;
    opacity: .035;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)'/%3E%3C/svg%3E");
}

footer { display: none !important; }

/* 滚动条：细、低调 */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, .10);
    border-radius: 99px;
    border: 3px solid transparent;
    background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, .18);
    background-clip: content-box;
}

/* ==========================================================
   Typography
   ========================================================== */

h1, h2, h3, h4 {
    letter-spacing: -.02em;
    font-weight: 650;
}

/* ==========================================================
   通用控件
   ========================================================== */

button, .gr-button {
    font-family: var(--sans) !important;
    font-size: 13px !important;
    font-weight: 550 !important;
    letter-spacing: -.005em;
    border-radius: var(--r-sm) !important;
    border: 1px solid var(--glass-bd) !important;
    background: var(--glass) !important;
    color: var(--tx) !important;
    min-height: 36px;
    backdrop-filter: blur(var(--blur-sm)) saturate(150%);
    -webkit-backdrop-filter: blur(var(--blur-sm)) saturate(150%);
    box-shadow: var(--gl-edge), var(--sh-sm) !important;
    transition: background .16s ease,
                border-color .16s ease,
                box-shadow .16s ease,
                transform .08s ease;
}

button:hover, .gr-button:hover {
    background: var(--glass-2) !important;
    border-color: var(--glass-bd-2) !important;
}

button:active, .gr-button:active {
    transform: translateY(.5px);
}

button:focus-visible, .gr-button:focus-visible {
    outline: none !important;
    box-shadow: 0 0 0 3px var(--accent-soft) !important;
    border-color: var(--accent-bd) !important;
}

button.primary, .gr-button.primary {
    background: linear-gradient(
        180deg,
        var(--accent-hi),
        var(--accent)
    ) !important;
    border: 1px solid rgba(255, 255, 255, .22) !important;
    color: #03150f !important;
    font-weight: 650 !important;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, .32),
        0 2px 6px rgba(0, 0, 0, .40),
        0 8px 22px rgba(16, 163, 127, .26) !important;
}
button.primary:hover, .gr-button.primary:hover {
    background: linear-gradient(
        180deg,
        #1fcb9e,
        #12ad88
    ) !important;
}

button.stop, .gr-button.stop {
    background: transparent !important;
    border-color: rgba(239, 106, 120, .34) !important;
    color: var(--err) !important;
    box-shadow: none !important;
}
button.stop:hover, .gr-button.stop:hover {
    background: rgba(239, 106, 120, .10) !important;
    border-color: rgba(239, 106, 120, .55) !important;
}

label, .gr-block label {
    font-size: 11.5px !important;
    font-weight: 550 !important;
    color: var(--muted) !important;
    letter-spacing: .02em;
}

input, textarea, select {
    font-family: var(--sans) !important;
    background: var(--glass) !important;
    backdrop-filter: blur(var(--blur-sm)) saturate(150%);
    -webkit-backdrop-filter: blur(var(--blur-sm)) saturate(150%);
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-sm) !important;
    color: var(--tx) !important;
    font-size: 13.5px !important;
    box-shadow: var(--gl-edge) !important;
}

input:focus, textarea:focus, select:focus {
    border-color: var(--accent-bd) !important;
    box-shadow: var(--gl-edge), 0 0 0 3px var(--accent-soft) !important;
    outline: none !important;
}

input::placeholder, textarea::placeholder {
    color: var(--faint) !important;
}

/* Gradio 6 的布局容器本身不应有底色和描边；
   需要卡片感的地方由具名 id 单独控制。 */
.block:not(.gr-accordion),
.form,
.container,
.column,
.row {
    background: transparent !important;
    border-color: transparent !important;
}

/* 左侧栏下拉框：统一成一种克制的输入壳 */
#left-panel .form {
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-sm) !important;
    background: var(--glass) !important;
    backdrop-filter: blur(var(--blur-sm)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--blur-sm)) saturate(160%);
    box-shadow: var(--gl-edge) !important;
    padding: 0 !important;
    overflow: hidden;
    margin-bottom: 2px;
}

#left-panel .form .container,
#left-panel .form .wrap,
#left-panel .form .wrap-inner,
#left-panel .form .secondary-wrap {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

#left-panel .form input,
#left-panel .form input.border-none {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: 13px !important;
    height: 34px !important;
}

#left-panel .form:focus-within {
    border-color: var(--accent-bd) !important;
    box-shadow: 0 0 0 3px var(--accent-soft) !important;
}

#left-panel .form .icon-wrap {
    color: var(--muted) !important;
    opacity: .85;
}

/* 手风琴真实类名是 gr-accordion */
.gr-accordion {
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-md) !important;
    background: var(--glass) !important;
    backdrop-filter: blur(var(--blur)) saturate(165%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(165%);
    overflow: hidden;
    box-shadow: var(--gl-edge), var(--sh-sm) !important;
}

.gr-accordion > .label-wrap,
.gr-accordion > button.label-wrap {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: var(--tx-2) !important;
    font-size: 12.5px !important;
    font-weight: 550 !important;
    min-height: 36px !important;
    padding: 0 12px !important;
    justify-content: flex-start !important;
    gap: 8px !important;
}

.gr-accordion > .label-wrap:hover {
    background: var(--panel-2) !important;
    color: var(--tx) !important;
}

.gr-accordion .icon {
    color: var(--muted) !important;
}

/* Gradio 的状态遮罩 / 隐藏层不该有任何底色 */
[data-testid="status-tracker"],
.wrap.hide,
.hide {
    background: transparent !important;
}

[data-testid="status-tracker"] {
    border: none !important;
    box-shadow: none !important;
}

/* 组件标签（含 Gradio 内置的隐藏 label）不显示白底 */
.block-label,
label[data-testid="block-label"] {
    background: transparent !important;
    color: var(--muted) !important;
}

.gr-checkbox label, .gr-radio label {
    color: var(--tx-2) !important;
    font-size: 13px !important;
}

/* ==========================================================
   Top Bar
   ========================================================== */

#topbar {
    min-height: 56px !important;
    height: 56px;
    padding: 0 20px !important;
    border-bottom: 1px solid var(--glass-bd) !important;
    background: linear-gradient(
        180deg,
        rgba(255, 255, 255, .075),
        rgba(255, 255, 255, .032)
    ) !important;
    backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    box-shadow:
        inset 0 -1px 0 rgba(255, 255, 255, .05),
        var(--sh-md) !important;
    align-items: center !important;
    gap: 14px;
    position: relative;
    z-index: 20;
}

#topbar > div { align-items: center !important; }

#brand-title { margin: 0 !important; }
#brand-title h3 {
    margin: 0 !important;
    font-size: 15.5px !important;
    font-weight: 680 !important;
    letter-spacing: -.025em;
    color: var(--tx) !important;
    display: inline-flex;
    align-items: center;
    gap: 9px;
}
#brand-title h3::before {
    content: "";
    width: 18px;
    height: 18px;
    border-radius: 6px;
    background: linear-gradient(
        140deg,
        var(--accent-hi),
        var(--accent-lo)
    );
    box-shadow:
        0 0 0 1px rgba(255, 255, 255, .12),
        0 3px 10px rgba(16, 163, 127, .38);
}
#brand-title p {
    margin: 0 !important;
    font-size: 11.5px !important;
    color: var(--faint) !important;
    letter-spacing: .01em;
}

#runtime-status {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 12px;
}

.chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 26px;
    padding: 0 11px;
    border-radius: 99px;
    background: var(--glass-2);
    border: 1px solid var(--glass-bd);
    backdrop-filter: blur(var(--blur-sm)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--blur-sm)) saturate(160%);
    box-shadow: var(--gl-edge);
    font-size: 11.5px;
    font-weight: 500;
    color: var(--tx-2);
    white-space: nowrap;
    line-height: 1;
    letter-spacing: -.005em;
}
.chip.ok { color: var(--ok); border-color: rgba(70, 199, 110, .26); }
.chip.warn { color: var(--warn); border-color: rgba(217, 161, 59, .30); }
.chip.mcp { color: var(--mcp); border-color: rgba(169, 123, 245, .26); }

.dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--muted);
    display: inline-block;
    flex: 0 0 auto;
}
.dot.ok {
    background: var(--ok);
    box-shadow: 0 0 0 3px rgba(70, 199, 110, .14);
}
.dot.busy {
    background: var(--accent);
    animation: pulse 1.15s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: .4; transform: scale(.8); }
}

#context-badge { margin: 0 !important; }
#context-badge p {
    margin: 0 !important;
    font-size: 11px !important;
    color: var(--faint) !important;
}

/* ==========================================================
   Shell / 三栏
   ========================================================== */

#topbar {
    flex: 0 0 56px !important;
}

#app-shell {
    flex: 1 1 auto !important;
    height: calc(100vh - 56px) !important;
    max-height: calc(100vh - 56px) !important;
    min-height: 0 !important;
    gap: 0 !important;
    align-items: stretch !important;
    position: relative;
    z-index: 5;
    overflow: hidden !important;
}

#left-panel,
#center-panel,
#right-panel {
    height: 100% !important;
    max-height: 100% !important;
    min-height: 0 !important;
    align-self: stretch !important;
}

#left-panel {
    width: 264px !important;
    flex: 0 0 264px !important;
    min-width: 264px !important;
    max-width: 264px !important;
    border-right: 1px solid var(--glass-bd) !important;
    background: linear-gradient(
        180deg,
        rgba(255, 255, 255, .062),
        rgba(255, 255, 255, .026)
    ) !important;
    backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    box-shadow:
        inset -1px 0 0 rgba(255, 255, 255, .045),
        28px 0 70px -42px rgba(0, 0, 0, .85),
        var(--sh-md) !important;
    padding: 16px 14px !important;
    gap: 6px !important;
    overflow-y: auto;
    overflow-x: hidden;
}

#center-panel {
    flex: 1 1 auto !important;
    width: auto !important;
    min-width: 0 !important;
    padding: 0 !important;
    gap: 0 !important;
    /* 中栏自带一层光晕。
       放在这里而不是全局层：全局光斑是 fixed + 低 z-index，
       到了中栏这块就画不进来了（中栏整片会发死黑）。
       自己带背景最稳，也让中栏内的玻璃件有东西可糊。 */
    background:
        radial-gradient(
            78% 48% at 6% 10%,
            rgba(16, 163, 127, .24),
            transparent 74%
        ),
        radial-gradient(
            74% 46% at 98% 16%,
            rgba(169, 123, 245, .22),
            transparent 76%
        ),
        radial-gradient(
            88% 52% at 56% 98%,
            rgba(46, 132, 255, .21),
            transparent 78%
        ),
        radial-gradient(
            66% 44% at 52% 50%,
            rgba(120, 150, 255, .115),
            transparent 76%
        ),
        radial-gradient(
            60% 42% at 14% 74%,
            rgba(255, 138, 96, .075),
            transparent 78%
        ),
        transparent !important;
    display: flex !important;
    flex-direction: column !important;
    position: relative;
    overflow: hidden !important;
}

#right-panel {
    width: 380px !important;
    flex: 0 0 380px !important;
    min-width: 380px !important;
    max-width: 380px !important;
    border-left: 1px solid var(--glass-bd) !important;
    background: linear-gradient(
        180deg,
        rgba(255, 255, 255, .062),
        rgba(255, 255, 255, .026)
    ) !important;
    backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    box-shadow:
        inset 1px 0 0 rgba(255, 255, 255, .045),
        -28px 0 70px -42px rgba(0, 0, 0, .85),
        var(--sh-md) !important;
    padding: 16px 14px !important;
    gap: 12px !important;
    overflow-y: auto;
    overflow-x: hidden;
}

/* 对话舞台：占据中栏剩余空间，是空状态的定位参照 */
#chat-stage {
    flex: 1 1 auto !important;
    min-height: 0 !important;
    height: auto !important;
    display: flex !important;
    flex-direction: column !important;
    padding: 0 !important;
    gap: 0 !important;
    background: transparent !important;
    position: relative;
    overflow: hidden !important;
}

/* 底部这些必须是固定高度，否则会被 flex 拉长 */
#approval-bar,
#composer-shell,
#composer-hint-row {
    flex: 0 0 auto !important;
}

.section-label {
    font-size: 10px;
    font-weight: 650;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: var(--faint);
    padding: 14px 4px 5px;
    line-height: 1.2;
}
.section-label:first-child { padding-top: 2px; }

/* Gradio 把 gr.HTML 包在 .block/.html-container 里，
   这些包裹层不能撑高，否则会顶开分组间距。 */
.block.hide-container,
.block.hide-container .wrap,
.block.hide-container .html-container {
    min-height: 0 !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    margin: 0 !important;
}

.block.hide-container .html-container > .prose,
.block.hide-container .prose {
    margin: 0 !important;
    padding: 0 !important;
}

/* 左栏整体：按钮更紧凑，避免长列表显得拥挤 */
#left-panel button {
    min-height: 34px !important;
    height: 34px !important;
    font-size: 12.5px !important;
    padding: 0 12px !important;
    justify-content: flex-start !important;
    text-align: left !important;
    box-shadow: var(--gl-edge) !important;
}

#left-panel button.primary {
    justify-content: center !important;
    text-align: center !important;
    min-height: 38px !important;
    height: 38px !important;
}

/* ==========================================================
   Welcome（空状态，覆盖在对话区上）
   ========================================================== */

#welcome {
    position: absolute;
    inset: 0;
    z-index: 6;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 32px;
    pointer-events: none;
    background: transparent;
}

/* 只要有对话内容，空状态就消失（CSS 层兜底，不依赖 JS） */
#chat-stage:has(#chatbot .message) #welcome,
#chat-stage:has(#chatbot .message-wrap) #welcome {
    display: none !important;
}

.welcome-inner {
    width: 100%;
    max-width: 720px;
    pointer-events: auto;
    animation: rise .45s cubic-bezier(.22, 1, .36, 1);
}

@keyframes rise {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: none; }
}

.welcome-mark {
    width: 46px;
    height: 46px;
    border-radius: 13px;
    background: linear-gradient(
        140deg,
        var(--accent-hi),
        var(--accent-lo)
    );
    box-shadow:
        0 0 0 1px rgba(255, 255, 255, .14),
        0 10px 30px rgba(16, 163, 127, .34);
    margin-bottom: 20px;
    position: relative;
}
.welcome-mark::after {
    content: "›";
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--mono);
    font-size: 24px;
    font-weight: 700;
    color: #04150f;
}

.welcome-title {
    font-size: 30px;
    font-weight: 680;
    letter-spacing: -.035em;
    line-height: 1.15;
    margin: 0 0 10px;
    color: var(--tx);
}

.welcome-sub {
    font-size: 14.5px;
    line-height: 1.65;
    color: var(--muted);
    margin: 0 0 26px;
    max-width: 520px;
}

.welcome-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    max-width: 720px;
}

.welcome-card {
    display: block;
    text-align: left;
    padding: 14px 16px !important;
    border: 1px solid var(--glass-bd) !important;
    background: linear-gradient(
        135deg,
        rgba(255, 255, 255, .075),
        rgba(255, 255, 255, .035)
    ) !important;
    backdrop-filter: blur(var(--blur)) saturate(170%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
    border-radius: var(--r-md) !important;
    cursor: pointer;
    box-shadow: var(--gl-edge), var(--sh-sm) !important;
    transition: background .18s ease,
                border-color .18s ease,
                box-shadow .18s ease,
                transform .14s ease;
    min-height: 0 !important;
    height: auto !important;
    width: 100% !important;
}
.welcome-card:hover {
    background: linear-gradient(
        135deg,
        rgba(255, 255, 255, .11),
        rgba(255, 255, 255, .055)
    ) !important;
    border-color: var(--accent-bd) !important;
    transform: translateY(-2px);
    box-shadow:
        var(--gl-edge),
        0 14px 34px rgba(0, 0, 0, .40) !important;
}
.welcome-card b {
    display: block;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--tx);
    margin-bottom: 4px;
    letter-spacing: -.01em;
}
.welcome-card span {
    display: block;
    font-size: 12px;
    line-height: 1.5;
    color: var(--muted);
}

/* ==========================================================
   Chat Stream
   ========================================================== */

#chatbot {
    /* Gradio 用它控制消息正文字号 */
    --chatbot-text-size: 15px;
    flex: 1 1 0 !important;
    min-height: 0 !important;
    height: auto !important;
    max-height: none !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
}

#chatbot > div,
#chatbot .wrapper,
#chatbot .bubble-wrap,
#chatbot .wrap {
    height: 100% !important;
    max-height: 100% !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

/* Gradio 6 聊天消息的真实结构：
   .bubble-wrap
     └ .message-wrap
        └ .message-row.bubble.{user,bot}-row     ← flex 行
           └ .flex-wrap.role
              └ .{user,bot}.message
                 └ .message.panel-full-width
                    └ #user / #bot
                       └ .message-content > span.md.chatbot.prose
           └ .message-buttons-{right,left}        ← 复制/重试按钮

   Gradio 默认让 user 气泡靠右、bot 靠左并各自内缩，
   这里把整条链路拉回「整宽正文流」，只靠前缀和左侧描边区分身份。 */
#chatbot .bubble-wrap {
    display: flex !important;
    flex-direction: column !important;
    width: 100% !important;
}

#chatbot .message-wrap {
    display: block !important;
    width: 100% !important;
    max-width: 100% !important;
    align-self: stretch !important;
}

#chatbot .message-row,
#chatbot .message-row.bubble,
#chatbot .message-row.user-row,
#chatbot .message-row.bot-row {
    display: block !important;
    position: static !important;
    width: 100% !important;
    max-width: 100% !important;
    align-self: stretch !important;
    justify-content: flex-start !important;
    padding: 0 !important;
    margin: 0 !important;
    background: transparent !important;
}

#chatbot .message-row > .flex-wrap,
#chatbot .flex-wrap,
#chatbot .role {
    display: block !important;
    width: 100% !important;
    max-width: 100% !important;
}

#chatbot .user.message,
#chatbot .bot.message,
#chatbot .message.panel-full-width,
#chatbot .message,
#chatbot #user,
#chatbot #bot {
    display: block !important;
    width: 100% !important;
    max-width: 100% !important;
    text-align: left !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
    margin: 0 !important;
}

#chatbot .message-content {
    display: block !important;
    width: 100% !important;
    max-width: 100% !important;
}

#chatbot .md,
#chatbot .md.chatbot.prose,
#chatbot .message .prose,
#chatbot .message .md {
    background: transparent !important;
    border: none !important;
    font-size: 15px !important;
    line-height: 1.72 !important;
    color: var(--tx) !important;
    overflow-wrap: break-word;
    max-width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* 消息容器统一宽度与左右留白 */
#chatbot .bubble-wrap {
    padding: 0 32px !important;
    max-width: 796px !important;
    margin: 0 auto !important;
}

/* chatbot 自带的处理中遮罩会盖出白色蒙层，关掉 */
#chatbot [data-testid="status-tracker"],
#chatbot .wrap.translucent {
    display: none !important;
}

#chatbot .md p { margin: 0 0 12px !important; }
#chatbot .md p:last-child { margin-bottom: 0 !important; }

#chatbot .md h1,
#chatbot .md h2,
#chatbot .md h3 {
    font-size: 16px !important;
    font-weight: 650 !important;
    letter-spacing: -.02em;
    margin: 20px 0 10px !important;
    color: var(--tx) !important;
}

#chatbot .md ul, #chatbot .md ol {
    margin: 0 0 12px !important;
    padding-left: 21px !important;
}
#chatbot .md li { margin: 4px 0 !important; }

#chatbot .md a {
    color: var(--accent-hi) !important;
    text-decoration: none;
    border-bottom: 1px solid rgba(23, 189, 147, .3);
}
#chatbot .md a:hover {
    border-bottom-color: var(--accent-hi);
}

#chatbot .md strong { color: #fff; font-weight: 620; }

#chatbot .md code {
    font-family: var(--mono) !important;
    font-size: 13px !important;
    background: var(--glass-2) !important;
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-xs);
    padding: 1.5px 6px !important;
    color: #e2e2ec !important;
}

#chatbot .md pre {
    background: var(--glass-deep) !important;
    backdrop-filter: blur(var(--blur)) saturate(150%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(150%);
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-md) !important;
    padding: 14px 16px !important;
    margin: 12px 0 !important;
    overflow-x: auto;
    box-shadow: var(--gl-edge), var(--sh-md) !important;
    /* 按内容宽度收缩：
       满宽时它会和用户气泡、表格左右边缘对齐成一条线，
       整段消息看上去像一整块拼接的板子。 */
    width: fit-content;
    max-width: 100%;
}
#chatbot .md pre code {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    font-size: 13px !important;
    line-height: 1.62 !important;
}

#chatbot .md table {
    border: 1px solid var(--hair) !important;
    border-radius: var(--r-sm);
    font-size: 13.5px !important;
    margin: 12px 0 !important;
    display: table;
    width: 100%;
}
#chatbot .md th, #chatbot .md td {
    border-color: var(--hair) !important;
    padding: 8px 12px !important;
}
#chatbot .md th {
    background: var(--panel-2) !important;
    font-weight: 600;
    color: var(--tx-2);
}

/* 引用块：只留一条细的强调边，不用整块底色 */
#chatbot .md blockquote {
    border: none !important;
    border-left: 2px solid var(--accent) !important;
    background: transparent !important;
    border-radius: 0;
    margin: 14px 0 !important;
    padding: 2px 0 2px 14px !important;
    color: var(--tx-2) !important;
}

#chatbot .md hr {
    border: none !important;
    border-top: 1px solid var(--hair) !important;
    margin: 20px 0 !important;
}

/* 用户消息：› 前缀 + 左侧强调边 */
#chatbot .message-row.user-row .md.chatbot.prose {
    background: linear-gradient(
        135deg,
        rgba(255, 255, 255, .085),
        rgba(255, 255, 255, .042)
    );
    backdrop-filter: blur(var(--blur)) saturate(170%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
    border: 1px solid var(--glass-bd);
    border-left: 2px solid var(--accent);
    border-radius: var(--r-md);
    padding: 12px 16px !important;
    margin: 22px 0 4px !important;
    box-shadow: var(--gl-edge), var(--sh-md);
    font-weight: 450;
    text-align: left !important;
}

#chatbot .message-row.user-row .md.chatbot.prose > p:first-child::before {
    content: "›";
    color: var(--accent-hi);
    font-family: var(--mono);
    font-weight: 700;
    margin-right: 10px;
}

/* Agent 消息：纯正文，无气泡 */
#chatbot .message-row.bot-row .md.chatbot.prose {
    padding: 8px 0 22px !important;
    margin: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

/* 最后一条助手消息下方留出呼吸空间 */
#chatbot .message-row.bot-row:last-child .md.chatbot.prose {
    padding-bottom: 8px !important;
}

#chatbot .avatar-container { display: none !important; }

/* 复制/重试小按钮：默认隐形，悬停才出现 */
#chatbot .message-buttons {
    display: flex !important;
    justify-content: flex-start !important;
    margin: 0 0 14px !important;
    height: 22px;
    opacity: 0;
    transition: opacity .16s ease;
}
#chatbot .message-wrap:hover .message-buttons {
    opacity: 1;
}

#chatbot .message-buttons button,
#chatbot .message-buttons .icon-button {
    min-height: 22px !important;
    height: 22px !important;
    width: auto !important;
    padding: 0 6px !important;
    background: transparent !important;
    border: 1px solid transparent !important;
    box-shadow: none !important;
    color: var(--faint) !important;
}
#chatbot .message-buttons button:hover {
    color: var(--tx-2) !important;
    border-color: var(--bd) !important;
    background: var(--panel) !important;
}

/* chatbot 自带的浮动图标按钮（顶部工具条、消息复制、代码块复制）
   它们的包装层默认带面板底色和描边，在对话流里会变成突兀的小方框。
   这里全部改成透明幽灵按钮，悬停才显出形态。 */
#chatbot .icon-button-wrapper,
#chatbot .icon-button-wrapper.top-panel,
#chatbot .icon-button-wrapper.hide-top-corner {
    background: transparent !important;
    border: none !important;
    border-width: 0 !important;
    box-shadow: none !important;
}

#chatbot .icon-button,
#chatbot .icon-button-wrapper button {
    background: transparent !important;
    border: 1px solid transparent !important;
    box-shadow: none !important;
    color: var(--faint) !important;
    min-height: 22px !important;
    height: 22px !important;
    min-width: 0 !important;
    padding: 0 5px !important;
}

#chatbot .icon-button:hover,
#chatbot .icon-button-wrapper button:hover {
    color: var(--tx) !important;
    background: var(--panel-2) !important;
    border-color: var(--bd) !important;
}

/* chatbot 顶部浮动工具条会贴在对话区右上角、
   和内容与顶栏都打架，直接收掉。
   整段对话的复制/导出走左侧「导出当前对话」。 */
#chatbot .icon-button-wrapper.top-panel {
    display: none !important;
}

/* Markdown 表格：hairline 网格，避免出现强调色描边 */
#chatbot .md table {
    border-collapse: collapse !important;
    border: 1px solid var(--hair) !important;
    border-radius: 0 !important;
    overflow: hidden;
    margin: 14px 0 !important;
    display: table !important;
    width: 100% !important;
}
#chatbot .md th,
#chatbot .md td {
    border: 1px solid var(--hair) !important;
    border-color: var(--hair) !important;
    padding: 8px 12px !important;
}
#chatbot .md thead th {
    background: var(--glass-2) !important;
    color: var(--tx-2) !important;
    font-weight: 600 !important;
    font-size: 12.5px !important;
}
#chatbot .md tbody tr:last-child td {
    border-bottom: 1px solid var(--hair) !important;
}

/* 代码块（放在后面是因为要覆盖 Gradio 的默认外壳底色） */
#chatbot .md pre,
#chatbot .md pre > div {
    background: var(--glass-deep) !important;
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-md) !important;
}

/* 思考过程 / 折叠面板 */
#chatbot .thought-group,
#chatbot .panel {
    border-color: var(--hair) !important;
    background: transparent !important;
}

/* ==========================================================
   Approval Card
   ========================================================== */

#approval-card {
    max-width: 760px;
    width: 100%;
    margin: 0 auto 10px;
    border: 1px solid rgba(217, 161, 59, .40) !important;
    background: linear-gradient(
        180deg,
        rgba(217, 161, 59, .16),
        rgba(217, 161, 59, .06)
    ) !important;
    backdrop-filter: blur(var(--blur)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(160%);
    border-radius: var(--r-md) !important;
    padding: 14px 18px !important;
    font-size: 13.5px !important;
    color: var(--tx) !important;
    line-height: 1.65 !important;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, .12),
        var(--sh-md) !important;
}
#approval-card h3 {
    margin: 0 0 9px !important;
    font-size: 13px !important;
    font-weight: 650 !important;
    color: var(--warn) !important;
    display: flex;
    align-items: center;
    gap: 8px;
    letter-spacing: -.01em;
}
#approval-card h3::before {
    content: "";
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--warn);
    animation: pulse 1.2s ease-in-out infinite;
    flex: 0 0 auto;
}
#approval-card code {
    font-family: var(--mono) !important;
    font-size: 12.5px !important;
    background: rgba(0, 0, 0, .3) !important;
    border: 1px solid var(--hair) !important;
    border-radius: var(--r-xs) !important;
    padding: 1px 6px !important;
}
#approval-card pre {
    background: rgba(0, 0, 0, .32) !important;
    border: 1px solid var(--hair) !important;
    border-radius: var(--r-sm);
    padding: 10px 13px !important;
    margin: 9px 0 0 !important;
    overflow-x: auto;
}
#approval-card pre code {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    white-space: pre-wrap;
    word-break: break-all;
}

#approval-bar {
    max-width: 796px;
    width: 100%;
    margin: 0 auto;
    padding: 0 32px !important;
    gap: 8px !important;
    align-items: center !important;
    justify-content: flex-end !important;
}

/* 没有任何待审批项时整条隐藏。
   Gradio 会把 visible=False 的组件从 DOM 移除，
   所以用 :has() 反查即可，无需改事件输出。 */
#center-panel:not(:has(#approval-card)) #approval-bar {
    display: none !important;
}

#approval-bar button {
    min-height: 34px !important;
    height: 34px !important;
    font-size: 12.5px !important;
    padding: 0 18px !important;
    flex: 0 0 auto !important;
    width: auto !important;
}

/* ==========================================================
   Composer
   ========================================================== */

#composer-shell {
    max-width: 796px;
    width: 100%;
    margin: 0 auto;
    padding: 4px 32px 20px;
}

#composer {
    background: linear-gradient(
        180deg,
        rgba(255, 255, 255, .088),
        rgba(255, 255, 255, .042)
    ) !important;
    backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--blur-lg)) saturate(180%);
    border: 1px solid var(--glass-bd-2) !important;
    border-radius: var(--r-xl) !important;
    padding: 11px 13px 9px !important;
    gap: 10px !important;
    align-items: flex-end !important;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, .16),
        inset 0 -1px 0 rgba(0, 0, 0, .18),
        0 18px 50px rgba(0, 0, 0, .44) !important;
    transition: border-color .18s ease,
                box-shadow .18s ease;
}
#composer:focus-within {
    border-color: var(--accent-bd) !important;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, .18),
        0 18px 50px rgba(0, 0, 0, .44),
        0 0 0 4px var(--accent-soft) !important;
}

#message-composer {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
}
#message-composer textarea {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    resize: none !important;
    font-size: 15px !important;
    line-height: 1.6 !important;
    color: var(--tx) !important;
    padding: 3px 4px !important;
    font-family: var(--sans) !important;
}
#message-composer textarea:focus {
    box-shadow: none !important;
    border: none !important;
}
#message-composer textarea::placeholder { color: var(--faint) !important; }

#send-task-button {
    min-height: 38px !important;
    min-width: 82px !important;
    border-radius: var(--r-md) !important;
    font-size: 13.5px !important;
}

#composer-hint-row {
    max-width: 796px;
    width: 100%;
    margin: 0 auto;
    padding: 10px 34px 0 !important;
    gap: 12px !important;
    align-items: center !important;
    justify-content: flex-end !important;
    /* 必须不换行，否则状态框会把提示和停止挤到第二行 */
    flex-wrap: nowrap !important;
}

#composer-hint {
    flex: 0 0 auto !important;
    width: auto !important;
    margin: 0 !important;
    font-size: 11.5px !important;
    color: var(--faint) !important;
    text-align: right !important;
    white-space: nowrap !important;
}
#composer-hint p { margin: 0 !important; font-size: 11.5px !important; }
#composer-hint strong { color: var(--muted); font-weight: 600; }

/* 停止按钮默认是安静的中性按钮，
   只在悬停时变红——避免空闲状态下一根红色按钮一直抢视线。 */
#stop-task-button {
    flex: 0 0 auto !important;
    width: auto !important;
    min-width: 0 !important;
    min-height: 26px !important;
    height: 26px !important;
    padding: 0 12px !important;
    font-size: 11.5px !important;
    background: transparent !important;
    border: 1px solid var(--hair) !important;
    color: var(--muted) !important;
}
#stop-task-button:hover {
    background: rgba(239, 106, 120, .10) !important;
    border-color: rgba(239, 106, 120, .45) !important;
    color: var(--err) !important;
}
/* 禁用态：明显压暗，悬停也不再变红，
   否则"不可点"和"可点"在视觉上分不出来。 */
#stop-task-button:disabled,
#stop-task-button[disabled] {
    opacity: .32 !important;
    cursor: default !important;
}
#stop-task-button:disabled:hover,
#stop-task-button[disabled]:hover {
    background: transparent !important;
    border-color: var(--hair) !important;
    color: var(--muted) !important;
}

/* 底部状态行：占满左侧剩余空间，
   把快捷键提示和停止按钮推到最右。 */
#status-line-box {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
    min-height: 26px !important;
    /* flex-basis 必须为 0：
       用 auto 时它会按内容撑开整行，把右侧两项顶到下一行。 */
    flex: 1 1 0 !important;
    min-width: 0 !important;
    max-width: 100% !important;
}
#status-line-box input,
#status-line-box textarea {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: var(--muted) !important;
    font-family: var(--mono) !important;
    font-size: 11.8px !important;
    padding: 0 !important;
    min-height: 26px !important;
    height: 26px !important;
}
#status-line-box input:focus,
#status-line-box textarea:focus { box-shadow: none !important; }

/* 快捷任务按钮。
   用 #left-panel 前缀提高优先级，
   否则会被上面的 #left-panel button 通用规则覆盖。 */
#left-panel .quick-btn {
    text-align: left !important;
    justify-content: flex-start !important;
    font-size: 12px !important;
    min-height: 32px !important;
    height: 32px !important;
    padding: 0 11px !important;
    background: transparent !important;
    border: 1px solid var(--hair) !important;
    color: var(--tx-2) !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    line-height: 1 !important;
    box-shadow: none !important;
}
#left-panel .quick-btn:hover {
    background: var(--panel-2) !important;
    border-color: var(--accent-bd) !important;
    color: var(--tx) !important;
}

/* 导出结果：只在导出后出现，做成一条紧凑的下载行 */
#export-file {
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-sm) !important;
    background: var(--glass) !important;
    backdrop-filter: blur(var(--blur-sm)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--blur-sm)) saturate(160%);
    box-shadow: var(--gl-edge) !important;
    padding: 0 !important;
    overflow: hidden;
}
#export-file label,
#export-file .block-label {
    display: none !important;
}
#export-file .file-preview,
#export-file a,
#export-file .file-name {
    font-family: var(--mono) !important;
    font-size: 11.5px !important;
    color: var(--accent-hi) !important;
}
#export-file button {
    min-height: 30px !important;
    height: 30px !important;
    font-size: 11.5px !important;
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
}

#export-status {
    margin: 0 !important;
}
#export-status p {
    margin: 2px 0 0 !important;
    font-size: 11px !important;
    color: var(--faint) !important;
}

#workspace-path { margin: 0 !important; }
#workspace-path p,
#workspace-path { font-size: 12px !important; }
#workspace-path code {
    font-family: var(--mono) !important;
    font-size: 11.2px !important;
    background: var(--panel) !important;
    border: 1px solid var(--hair) !important;
    border-radius: var(--r-xs) !important;
    padding: 5px 9px !important;
    color: var(--muted) !important;
    display: block;
    word-break: break-all;
}

/* ==========================================================
   Right Panel
   ========================================================== */

/* Gradio 6 的 Tab 真实结构：
   .tabs > .tab-wrapper > .tab-container > button(.selected)
   内容在 .tabs > .tabitem */
#right-panel .tabs,
#settings-shell .tabs {
    border: none !important;
    background: transparent !important;
    gap: 12px !important;
}

#right-panel .tab-wrapper,
#settings-shell .tab-wrapper {
    position: relative;
}

#right-panel .tab-container:not(.visually-hidden),
#settings-shell .tab-container:not(.visually-hidden) {
    display: flex !important;
    align-items: center !important;
    gap: 2px !important;
    background: var(--glass) !important;
    backdrop-filter: blur(var(--blur)) saturate(165%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(165%);
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-md) !important;
    padding: 3px !important;
    width: 100% !important;
    box-sizing: border-box;
    box-shadow: var(--gl-edge) !important;
}

#right-panel .tab-container:not(.visually-hidden) button,
#settings-shell .tab-container:not(.visually-hidden) button {
    flex: 1 1 auto !important;
    border: none !important;
    background: transparent !important;
    color: var(--muted) !important;
    border-radius: var(--r-sm) !important;
    font-size: 12.5px !important;
    font-weight: 550 !important;
    padding: 0 8px !important;
    height: 30px !important;
    min-height: 30px !important;
    box-shadow: none !important;
    white-space: nowrap !important;
    transition: background .14s ease, color .14s ease;
}

#right-panel .tab-container:not(.visually-hidden) button:hover,
#settings-shell .tab-container:not(.visually-hidden) button:hover {
    background: var(--panel-2) !important;
    color: var(--tx) !important;
}

#right-panel .tab-container:not(.visually-hidden) button.selected,
#settings-shell .tab-container:not(.visually-hidden) button.selected {
    background: var(--glass-3) !important;
    color: var(--tx) !important;
    box-shadow: var(--gl-edge), var(--sh-sm) !important;
}

#right-panel .tabitem,
#settings-shell .tabitem {
    border: none !important;
    padding: 0 !important;
    background: transparent !important;
    flex-direction: column !important;
    gap: 12px !important;
    min-height: 0 !important;
}

#trace-hint { margin: 0 0 12px !important; }
#trace-hint p {
    margin: 0 !important;
    font-size: 12px !important;
    color: var(--muted) !important;
    line-height: 1.65 !important;
}

#trace-table {
    font-size: 12px !important;
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-md) !important;
    overflow: hidden;
    backdrop-filter: blur(var(--blur)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(160%);
    box-shadow: var(--gl-edge), var(--sh-sm) !important;
}
#trace-table table { font-size: 12px !important; }
#trace-table thead th {
    background: var(--glass-2) !important;
    color: var(--muted) !important;
    font-weight: 600 !important;
    font-size: 10.5px !important;
    text-transform: uppercase;
    letter-spacing: .07em;
    border-bottom: 1px solid var(--hair) !important;
}
#trace-table tbody td {
    border-bottom: 1px solid var(--hair) !important;
    font-family: var(--mono);
    font-size: 11.5px !important;
    line-height: 1.55 !important;
    padding: 8px 10px !important;
    color: var(--tx-2) !important;
    vertical-align: top;
}
#trace-table tbody tr:hover td { background: var(--panel) !important; }

#tools-box, #sources-box, #context-box {
    font-size: 13px !important;
    line-height: 1.7 !important;
    color: var(--tx-2) !important;
}
#tools-box code, #sources-box code, #context-box code {
    font-family: var(--mono) !important;
    font-size: 12px !important;
    background: var(--panel-2) !important;
    border: 1px solid var(--hair) !important;
    border-radius: var(--r-xs);
    padding: 1px 6px !important;
}
#tools-box h3, #sources-box h3, #context-box h3 {
    font-size: 13.5px !important;
    margin: 14px 0 7px !important;
    color: var(--tx) !important;
    letter-spacing: -.015em;
}
#tools-box ul, #sources-box ul, #context-box ul {
    padding-left: 19px !important;
    margin: 5px 0 !important;
}

/* ==========================================================
   Settings
   ========================================================== */

/* Settings 的 Tab 样式与右栏共用上面的规则，
   这里只补它自己的间距和分组卡片。 */
#settings-shell {
    padding: 24px 28px 40px !important;
    gap: 18px !important;
    overflow-y: auto !important;
}

#settings-shell .gr-accordion,
#settings-shell .form {
    border: 1px solid var(--glass-bd) !important;
    border-radius: var(--r-md) !important;
    background: var(--glass) !important;
    backdrop-filter: blur(var(--blur)) saturate(165%);
    -webkit-backdrop-filter: blur(var(--blur)) saturate(165%);
    box-shadow: var(--gl-edge), var(--sh-sm) !important;
    padding: 14px 16px !important;
}

#settings-shell .gr-accordion .label-wrap {
    font-weight: 600 !important;
}

/* ==========================================================
   Responsive
   ========================================================== */

@media (max-width: 1400px) {
    #right-panel {
        width: 344px !important;
        flex: 0 0 344px !important;
        min-width: 344px !important;
        max-width: 344px !important;
    }
}

@media (max-width: 1180px) {
    #right-panel {
        width: 300px !important;
        flex: 0 0 300px !important;
        min-width: 300px !important;
        max-width: 300px !important;
    }
    #left-panel {
        width: 232px !important;
        flex: 0 0 232px !important;
        min-width: 232px !important;
        max-width: 232px !important;
    }
}

@media (max-width: 960px) {
    #left-panel { display: none !important; }
    #right-panel { display: none !important; }
    #chatbot .md,
    #composer-shell, #composer-hint-row,
    #approval-bar, #approval-card {
        padding-left: 20px;
        padding-right: 20px;
    }
    #welcome { padding: 0 20px 100px; }
    .welcome-grid { grid-template-columns: 1fr; }
}
"""

# ============================================================
# UI
# ============================================================

# ============================================================
# 右栏 Tab 修正
#
# 必须追加在 GLASS_CSS 之后：GLASS_CSS 用 !important 且在其之前，
# 写在主 CSS 里会被它整段压掉。
# ============================================================

RIGHT_PANEL_FIX_CSS = """
/* ------------------------------------------------------------
   关键修复：禁止面板换列
   Gradio 的 .column 自带 flex-wrap: wrap，而三栏面板是
   flex-direction: column + 固定高度 + overflow-x: hidden。
   于是当某个 Tab 的内容比面板高时，溢出部分会被"换行"到
   右侧新起的一列 → 整块被 overflow-x 裁掉 → 面板一片空白。
   表现：右栏只有第一个 Tab 有内容，切到其余 Tab 全白。

   强制 nowrap，让内容改走纵向滚动。
   ------------------------------------------------------------ */

#left-panel,
#center-panel,
#right-panel {
    flex-wrap: nowrap !important;
}

/* 面板子项禁止被压缩。
   面板是固定高度的 flex 列容器，内容一超高，
   flex-shrink 会把子项压扁；而 Gradio 的块内部是
   position:absolute（靠 --start-* 变量定位），
   压扁后内层不会跟着缩，直接和相邻块叠在一起
   （表现：标题被 Tab 栏压住）。
   正确行为是内容溢出 → 交给 overflow-y 滚动。 */
#left-panel > *,
#right-panel > * {
    flex-shrink: 0 !important;
}

/* 面板内的 Tab 容器不该被压缩或撑宽 */
#right-panel > .tabs,
#settings-shell > .tabs {
    flex: 0 0 auto !important;
    min-width: 0 !important;
    max-width: 100% !important;
}

/* Gradio 用一组隐藏的"测量按钮"判断 Tab 是否放得下；
   测量按钮与真实按钮宽度不一致时，它会判定放不下
   并把整排 Tab 折叠成下拉框（视觉上就是"Tab 不见了"）。
   这里把右栏所有 Tab（含设置页里嵌套的那层）
   统一压到同一尺寸，让测量值与真实值一致。 */
#right-panel .tab-container button,
#right-panel .tabs .tab-container button {
    font-size: 12px !important;
    padding: 0 8px !important;
    min-width: 0 !important;
    flex: 0 1 auto !important;
}

#right-panel .tab-container:not(.visually-hidden),
#right-panel .tabs .tab-container:not(.visually-hidden) {
    gap: 2px !important;
    flex-wrap: nowrap !important;
    overflow: visible !important;
}

#right-panel .tabitem { min-height: 0; }
"""

# ============================================================
# 输入框「+」菜单 / 拖拽上传
#
# 放在 GLASS_CSS 之后追加：GLASS_CSS 全量 !important，
# 写在主 CSS 里会被整段压掉。
# ============================================================

COMPOSER_MENU_HTML = r"""
<div class="composer-menu" data-menu hidden>
  <button type="button" data-action="upload">
    <span class="cm-icon">＋</span>
    <span class="cm-text">上传附件<small>导入工作区，路径写入输入框</small></span>
  </button>
  <button type="button" data-action="folder">
    <span class="cm-icon">▤</span>
    <span class="cm-text">导入本地文件夹<small>填写绝对路径后导入</small></span>
  </button>
  <div class="cm-folder" data-folder hidden>
    <input type="text" placeholder="D:\projects\demo" data-folder-input />
    <button type="button" data-action="folder-go">导入</button>
  </div>
  <div class="cm-sep"></div>
  <button type="button" data-action="open">
    <span class="cm-icon">▢</span>
    <span class="cm-text">打开工作区目录</span>
  </button>
  <button type="button" data-action="export">
    <span class="cm-icon">↧</span>
    <span class="cm-text">导出当前对话</span>
  </button>
  <button type="button" data-action="clear">
    <span class="cm-icon">⌫</span>
    <span class="cm-text">清空当前显示</span>
  </button>
  <button type="button" data-action="plugins">
    <span class="cm-icon">⬡</span>
    <span class="cm-text">插件管理</span>
  </button>
</div>
"""

COMPOSER_EXTRA_CSS = """
/* 由「+」「×」这类纯符号按钮触发的隐藏组件，
   不能用 visible=False（Gradio 会直接把它移出 DOM，
   JS 就拿不到它了），只能靠 CSS 藏。 */
.xiaozhi-hidden { display: none !important; }

#composer-shell { position: relative; }

/* 「+」按钮：与发送按钮同高，贴着输入框左下角 */
#composer-plus {
    flex: 0 0 auto !important;
    width: 38px !important;
    min-width: 38px !important;
    height: 38px !important;
    min-height: 38px !important;
    padding: 0 !important;
    align-self: flex-end !important;
    border-radius: var(--r-md) !important;
}
#composer-plus {
    color: var(--tx-2) !important;
    border: 1px solid var(--hair) !important;
    background: var(--glass) !important;
    box-shadow: none !important;
    transition: border-color .16s ease, color .16s ease,
                background .16s ease, transform .16s ease;
}
#composer-plus:hover,
#composer-plus.menu-open {
    border-color: var(--accent-bd) !important;
    background: var(--accent-soft) !important;
    color: var(--accent-hi) !important;
}
#composer-plus.menu-open { transform: rotate(45deg); }
#composer-plus button,
#composer-plus > button {
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    padding: 0 !important;
    font-size: 20px !important;
    line-height: 1 !important;
    justify-content: center !important;
    border-radius: var(--r-md) !important;
}

/* 菜单本体：锚在 composer-shell 上，向上弹出 */
.composer-menu {
    position: absolute;
    left: 46px;
    bottom: 62px;
    z-index: 60;
    width: 272px;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    border: 1px solid var(--bd-2);
    border-radius: var(--r-lg);
    background: rgba(14, 20, 31, .92);
    backdrop-filter: blur(20px) saturate(175%);
    -webkit-backdrop-filter: blur(20px) saturate(175%);
    box-shadow: 0 20px 54px rgba(0, 0, 0, .55);
    animation: cm-pop .14s ease-out;
}
@keyframes cm-pop {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}
.composer-menu[hidden],
.composer-menu .cm-folder[hidden] { display: none !important; }

.composer-menu button {
    display: flex !important;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 9px 10px;
    border: none !important;
    background: transparent !important;
    color: var(--tx-2) !important;
    font-size: 13px !important;
    font-family: var(--sans) !important;
    text-align: left !important;
    cursor: pointer;
    border-radius: var(--r-sm) !important;
    box-shadow: none !important;
    min-height: 0 !important;
    height: auto !important;
    justify-content: flex-start !important;
}
.composer-menu button:hover {
    background: var(--panel-2) !important;
    color: var(--tx) !important;
}
.composer-menu .cm-icon {
    flex: 0 0 auto;
    width: 23px;
    height: 23px;
    display: grid;
    place-items: center;
    border-radius: 7px;
    border: 1px solid var(--hair);
    background: var(--glass-2);
    font-size: 12px;
}
.composer-menu .cm-text { display: flex; flex-direction: column; gap: 2px; }
.composer-menu .cm-text small { color: var(--faint); font-size: 11px; line-height: 1.4; }
.composer-menu .cm-sep { height: 1px; background: var(--hair); margin: 4px 6px; }

.composer-menu .cm-folder {
    display: flex;
    gap: 6px;
    padding: 6px 8px 8px;
}
.composer-menu .cm-folder input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 6px 9px;
    border: 1px solid var(--bd) !important;
    border-radius: var(--r-sm);
    background: var(--panel) !important;
    color: var(--tx) !important;
    font-family: var(--mono);
    font-size: 12px;
}
.composer-menu .cm-folder input:focus {
    border-color: var(--accent-bd) !important;
    box-shadow: 0 0 0 3px var(--accent-soft) !important;
}
.composer-menu .cm-folder button {
    width: auto !important;
    flex: 0 0 auto;
    padding: 6px 13px !important;
    border: 1px solid var(--accent-bd) !important;
    background: var(--accent-soft) !important;
    color: var(--accent-hi) !important;
    font-size: 12px !important;
}

/* 拖拽上传覆盖层 */
#xiaozhi-dropzone {
    position: fixed;
    inset: 0;
    z-index: 9000;
    display: none;
    align-items: center;
    justify-content: center;
    background: rgba(6, 10, 17, .52);
    backdrop-filter: blur(7px);
    -webkit-backdrop-filter: blur(7px);
}
#xiaozhi-dropzone.visible { display: flex; }
#xiaozhi-dropzone .dz-card {
    border: 2px dashed rgba(23, 189, 147, .8);
    border-radius: 22px;
    padding: 36px 52px;
    text-align: center;
    background: rgba(11, 32, 27, .78);
    box-shadow: 0 20px 60px rgba(0, 0, 0, .5);
    pointer-events: none;
}
#xiaozhi-dropzone .dz-card b {
    display: block;
    font-size: 18px;
    font-weight: 600;
    color: #eafff8;
    letter-spacing: -.01em;
}
#xiaozhi-dropzone .dz-card span {
    display: block;
    margin-top: 8px;
    font-size: 12.5px;
    color: #a9c9bf;
}

/* 上传控件本身不露面：入口是「+」菜单和拖拽。
   用离屏而不是 display:none——个别浏览器会拒绝
   对 display:none 的 file input 触发选择框。 */
#composer-upload {
    position: fixed !important;
    left: -9999px !important;
    top: 0 !important;
    width: 1px !important;
    height: 1px !important;
    min-width: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

/* ------------------------------------------------------------
   左栏：分组、工作区卡片、插件卡片
   ------------------------------------------------------------ */

/* 分组标签之上加一条发丝线，让左栏读起来是"几段"而不是一片 */
#left-panel .section-label.rule {
    border-top: 1px solid var(--hair);
    margin-top: 14px;
}

/* 手风琴标题（如"重命名 / 删除会话"）要保持中性色：
   Gradio 主题给内层 span 上了链接蓝，看着像可跳转的文字。 */
#left-panel .gr-accordion .label-wrap span:not(.icon) {
    color: var(--tx-2) !important;
    font-weight: 550 !important;
}
#left-panel .gr-accordion .label-wrap:hover span:not(.icon) {
    color: var(--tx) !important;
}

/* 次要动作并排：等宽两列，居中文字 */
#left-panel .side-row {
    gap: 6px !important;
    align-items: stretch !important;
    flex-wrap: nowrap !important;
}
#left-panel .side-row > * {
    flex: 1 1 0 !important;
    min-width: 0 !important;
}
#left-panel .side-row button {
    justify-content: center !important;
    text-align: center !important;
    padding: 0 8px !important;
}

/* 工作区卡片：短名 + 文件数，下面一行完整路径（超长两行截断）
   左栏模型状态卡复用同一套结构，所以选择器并列写。 */
#workspace-path .side-path,
#model-status .side-path {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
}
#workspace-path .side-path-name,
#model-status .side-path-name {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--tx-2);
}
#workspace-path .side-path-count,
#model-status .side-path-count {
    font-size: 11.5px;
    color: var(--muted);
}
/* 未配置接口：名字用警示色，让人一眼看出这里有问题 */
#model-status .side-path-name.warn {
    font-family: inherit;
    color: var(--err);
    font-weight: 600;
}
#model-status .side-path-count {
    white-space: nowrap;
}
/* 完整路径只占一行：超出用省略号收尾，完整值挂在 title 上。
   之前用 break-all 换行，会在 "workspa / ce" 中间硬断，很别扭。 */
#workspace-path .side-path-full,
#model-status .side-path-full {
    margin-top: 5px;
    font-family: var(--mono);
    font-size: 10.5px;
    line-height: 1.5;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    cursor: default;
}

#left-panel .side-stat,
#plugin-summary .side-stat {
    font-size: 11.5px !important;
    line-height: 1.6;
    color: var(--muted) !important;
}
#left-panel .side-stat b,
#plugin-summary .side-stat b {
    color: var(--accent-hi);
    font-weight: 650;
}

/* ------------------------------------------------------------
   右栏 5 个 Tab（进度 / 文件 / 能力 / 插件 / 设置）
   按钮内边距收紧，保证一排放得下、又不触发折叠成下拉框
   ------------------------------------------------------------ */

#inspector-tabs > .tab-wrapper .tab-container:not(.visually-hidden) button {
    font-size: 12.5px !important;
    padding: 0 6px !important;
    height: 32px !important;
    min-width: 0 !important;
}

/* 卸载是低频破坏性操作，安静一点：
   平时只是描边红字，悬停才实心，和「停止」按钮保持同一套语言。 */
#plugin-uninstall-button {
    background: transparent !important;
    border: 1px solid var(--hair) !important;
    color: var(--muted) !important;
    box-shadow: none !important;
}
#plugin-uninstall-button:hover {
    background: rgba(239, 106, 120, .10) !important;
    border-color: rgba(239, 106, 120, .45) !important;
    color: var(--err) !important;
}

#plugin-intro p, #plugin-detail p, #plugin-status p { margin: 0 !important; }
#plugin-detail h3 { margin: 0 0 8px !important; font-size: 14px !important; }
#plugin-detail ul { padding-left: 18px !important; margin: 4px 0 !important; }
#plugin-table { font-size: 12px !important; }
"""

CSS += GLASS_CSS
CSS += RIGHT_PANEL_FIX_CSS
CSS += COMPOSER_EXTRA_CSS

with gr.Blocks(
    title="小智 Agent",
) as demo:

    trace_state = (
        gr.State([])
    )

    # ========================================================
    # Top Product Bar
    # ========================================================

    with gr.Row(
        elem_id="topbar",
    ):

        with gr.Column(
            scale=5,
            min_width=320,
        ):

            gr.Markdown(
                "### 小智\n本地智能体工作台",
                elem_id="brand-title",
            )

        with gr.Column(
            scale=3,
            min_width=240,
        ):

            runtime_status = (
                gr.HTML(
                    value=(
                        runtime_status_markdown()
                    ),
                    elem_id="runtime-status",
                )
            )

            context_badge = (
                gr.Markdown(
                    value=(
                        context_badge_text()
                    ),
                    elem_id="context-badge",
                )
            )

    with gr.Row(
        elem_id="app-shell",
    ):

        # ====================================================
        # Left Sidebar
        # ====================================================

        with gr.Column(
            scale=2,
            min_width=230,
            elem_id="left-panel",
        ):

            # ------------------------------------------------
            # 主操作：新任务是唯一的主按钮，
            # 清空显示降为次级，避免两个按钮抢注意力。
            # ------------------------------------------------

            gr.HTML('<div class="shell-sidebar-heading"><span>会话与工具</span><button type="button" class="shell-icon" data-shell="close" aria-label="关闭会话侧栏">×</button></div>')

            new_session_button = (
                gr.Button(
                    "+ 新任务",
                    variant="primary",
                    elem_id="new-task-button",
                )
            )

            gr.HTML('''<nav class="workbench-nav" aria-label="工作台导航">
                <button type="button" data-workbench-tab="进度"><span aria-hidden="true">◷</span>任务进度</button>
                <button type="button" data-workbench-tab="文件"><span aria-hidden="true">▤</span>工作区文件</button>
                <button type="button" data-workbench-tab="插件"><span aria-hidden="true">◇</span>插件与扩展</button>
                <button type="button" data-workbench-tab="设置"><span aria-hidden="true">⚙</span>设置与外观</button>
            </nav>''')

            gr.HTML(
                '<div class="section-label rule">'
                "会话"
                "</div>"
            )

            conversation_selector = (
                gr.Dropdown(
                    choices=conversation_choices(),
                    value=service.get_session_id(),
                    label=None,
                    show_label=False,
                    interactive=True,
                    filterable=True,
                )
            )

            # 会话相关的两个次要动作并成一行，
            # 避免左栏出现一排等宽的整行按钮（最丑的一种栏）。
            with gr.Row(
                elem_classes=[
                    "side-row"
                ],
            ):

                refresh_conversations_button = (
                    gr.Button(
                        "刷新列表"
                    )
                )

                clear_chat_button = (
                    gr.Button(
                        "清空显示",
                        elem_id="clear-display-button",
                    )
                )

            with gr.Accordion(
                "重命名 / 删除会话",
                open=False,
            ):

                conversation_title = (
                    gr.Textbox(
                        value=(
                            current_conversation_title()
                        ),
                        label="会话名称",
                        placeholder="输入会话名称……",
                    )
                )

                rename_conversation_button = (
                    gr.Button(
                        "重命名"
                    )
                )

                delete_confirm = (
                    gr.Checkbox(
                        value=False,
                        label="确认删除当前对话",
                    )
                )

                delete_conversation_button = (
                    gr.Button(
                        "删除当前对话",
                        variant="stop",
                    )
                )

            with gr.Accordion("工具与运行配置", open=False):
                gr.HTML(
                    '<div class="section-label rule">'
                    "快捷任务"
                    "</div>"
                )

                quick_buttons = []

                for _title, _prompt in (
                    QUICK_PROMPTS[:4]
                ):

                    quick_buttons.append(
                        gr.Button(
                            _title,
                            elem_classes=[
                                "quick-btn"
                            ],
                        )
                    )

                gr.HTML(
                    '<div class="section-label rule">'
                    "模型与接口"
                    "</div>"
                )

                model_status_left = (
                    gr.HTML(
                        model_status_markdown(),
                        elem_id="model-status",
                    )
                )

                open_model_settings_button = (
                    gr.Button(
                        "配置接口 / API Key",
                        elem_id="open-model-settings-button",
                    )
                )

                gr.HTML(
                    '<div class="section-label rule">'
                    "工作区"
                    "</div>"
                )

                workspace_path = (
                    gr.HTML(
                        workspace_summary_markdown(),
                        elem_id="workspace-path",
                    )
                )

                with gr.Row(
                    elem_classes=[
                        "side-row"
                    ],
                ):

                    export_button = (
                        gr.Button(
                            "导出对话",
                            elem_id="export-button",
                        )
                    )

                    open_workspace_button = (
                        gr.Button(
                            "打开目录",
                            elem_id="open-workspace-button",
                        )
                    )

                export_file = (
                    gr.File(
                        label="导出文件",
                        visible=False,
                        elem_id="export-file",
                    )
                )

                export_status = (
                    gr.Markdown(
                        value="",
                        elem_id="export-status",
                    )
                )

                gr.HTML(
                    '<div class="section-label rule">'
                    "插件"
                    "</div>"
                )

                plugin_summary_left = (
                    gr.HTML(
                        plugin_summary_markdown(),
                        elem_classes=[
                            "side-stat"
                        ],
                    )
                )

                manage_plugin_button = (
                    gr.Button(
                        "管理插件",
                        elem_id="manage-plugin-button",
                    )
                )

        # ====================================================
        # Center Chat
        # ====================================================

        with gr.Column(
            scale=7,
            min_width=520,
            elem_id="center-panel",
        ):

            # --------------------------------------------
            # Chat Stage：对话流 + 空状态
            # 两者共用一个可伸缩容器，
            # 空状态绝对定位只覆盖这一块，
            # 永远不会盖住下面的审批栏和输入框。
            # --------------------------------------------

            gr.HTML('''<div class="conversation-heading">
                <div class="shell-title"><button type="button" class="shell-icon mobile-sidebar" data-shell="sidebar" aria-label="展开会话侧栏" aria-controls="left-panel" aria-expanded="false">☰</button><span>工作对话</span></div>
                <div class="shell-actions"><button type="button" data-workbench-tab="文件">文件</button><button type="button" data-workbench-tab="设置">设置与外观</button><button type="button" data-shell="inspector" aria-controls="right-panel" aria-expanded="false">工作台</button></div>
            </div>''')

            with gr.Column(
                elem_id="chat-stage",
            ):

                chatbot = (
                    gr.Chatbot(
                        value=[],
                        label="",
                        show_label=False,
                        elem_id="chatbot",
                        placeholder=None,
                    )
                )

                welcome = (
                    gr.HTML(
                        value=welcome_html(),
                        elem_id="welcome",
                    )
                )

            # --------------------------------------------
            # Inline Approval（内联在输入框上方）
            # --------------------------------------------

            approval_box = (
                gr.Markdown(
                    value="",
                    visible=False,
                    elem_id="approval-card",
                )
            )

            with gr.Row(
                elem_id="approval-bar",
            ):

                approve_button = (
                    gr.Button(
                        "批准并执行",
                        variant="primary",
                        interactive=False,
                    )
                )

                reject_button = (
                    gr.Button(
                        "拒绝",
                        variant="stop",
                        interactive=False,
                    )
                )

                # 「本次会话内自动放行此类操作」
                #
                # 内置写工具的 needs_approval 是静态写死的，
                # 改 10 个文件原本要连点 10 次批准。
                # 勾选后该工具在本次会话内不再打断，
                # 新建会话即失效，不会跨会话保留授权。
                auto_approve_checkbox = (
                    gr.Checkbox(
                        label=(
                            "本次会话内自动放行"
                            "此类操作"
                        ),
                        value=False,
                        elem_id=(
                            "auto-approve-toggle"
                        ),
                        scale=1,
                        min_width=220,
                    )
                )

            # --------------------------------------------
            # Composer
            # --------------------------------------------

            with gr.Column(
                elem_id="composer-shell",
            ):

                with gr.Row(
                    elem_id="composer",
                ):

                    plus_button = (
                        gr.Button(
                            "＋",
                            scale=0,
                            min_width=38,
                            elem_id="composer-plus",
                        )
                    )

                    message_box = (
                        gr.Textbox(
                            value="",
                            label="",
                            show_label=False,
                            container=False,
                            placeholder=(
                                "给小智一个任务……"
                            ),
                            lines=3,
                            max_lines=9,
                            scale=10,
                            elem_id="message-composer",
                        )
                    )

                    send_button = (
                        gr.Button(
                            "发送",
                            variant="primary",
                            scale=0,
                            min_width=76,
                            elem_id="send-task-button",
                        )
                    )

                # --------------------------------------------
                # 「+」菜单本体。
                # 菜单里的动作要么走 JS，
                # 要么去点下面这些不露面的组件。
                # --------------------------------------------

                gr.HTML(
                    COMPOSER_MENU_HTML,
                    elem_id="composer-menu",
                )

                composer_upload = (
                    gr.File(
                        label="任务附件",
                        file_count="multiple",
                        type="filepath",
                        elem_id="composer-upload",
                    )
                )

                folder_quick_path = (
                    gr.Textbox(
                        value="",
                        label="",
                        show_label=False,
                        container=False,
                        elem_id="folder-quick-path",
                        elem_classes=[
                            "xiaozhi-hidden"
                        ],
                    )
                )

                folder_quick_import = (
                    gr.Button(
                        "导入",
                        elem_id="quick-folder-btn",
                        elem_classes=[
                            "xiaozhi-hidden"
                        ],
                    )
                )

            with gr.Row(
                elem_id="composer-hint-row",
            ):

                status_box = (
                    gr.Textbox(
                        value="就绪",
                        label="",
                        show_label=False,
                        container=False,
                        interactive=False,
                        scale=6,
                        elem_id="status-line-box",
                    )
                )

                gr.Markdown(
                    "**Enter** 发送 · "
                    "**Shift + Enter** 换行",
                    elem_id="composer-hint",
                    scale=0,
                )

                # 停止按钮初始不可点：
                # 只有任务真的在跑才有意义，
                # 空闲时挂着一根可点的按钮是误导。
                stop_button = (
                    gr.Button(
                        "停止",
                        variant="stop",
                        scale=0,
                        interactive=False,
                        elem_id="stop-task-button",
                    )
                )

        # ====================================================
        # Right Product Panel
        # ====================================================

        with gr.Column(
            scale=3,
            min_width=330,
            elem_id="right-panel",
        ):

            gr.HTML('<div class="inspector-heading"><span>工作台</span><button type="button" class="shell-icon" data-shell="close" aria-label="关闭工作台">×</button></div>')

            with gr.Tabs(elem_id="inspector-tabs"):

                with gr.Tab('进度'):
                    plan_box = gr.Markdown(value=task_plan_markdown())
                    refresh_plan_button = gr.Button("刷新任务计划")

                    gr.Markdown(
                        "任务执行时，每一步工具调用"
                        "都会实时记录在这里。\n\n"
                        "需要你确认的操作会直接出现在"
                        "输入框上方。",
                        elem_id="trace-hint",
                    )

                    trace_table = (
                        gr.Dataframe(
                            headers=(
                                TRACE_HEADERS
                            ),
                            datatype=[
                                "str",
                                "str",
                                "str",
                                "str",
                            ],
                            value=[],
                            interactive=False,
                            wrap=True,
                            max_height=460,
                            elem_id="trace-table",
                        )
                    )

                    with gr.Accordion('参考来源', open=False):
                        sources_box = (
                            gr.Markdown(
                                value=(
                                    empty_sources_text()
                                ),
                                elem_id="sources-box",
                            )
                        )

                with gr.Tab('文件'):
                    with gr.Column(
                        elem_id="files-panel",
                    ):

                        gr.Markdown("上传附件后点击导入，文件路径会加入输入框。每次最多 10 个，每个 ≤ 20 MB。")
                        attachment_files = gr.File(label="任务附件", file_count="multiple", type="filepath")
                        import_files_button = gr.Button("导入附件到工作区", variant="primary")
                        attachment_status = gr.Markdown("")

                        gr.Markdown(
                            "---\n\n"
                            "**导入本地文件夹**\n\n"
                            "填写本机绝对路径，先扫描确认再导入。"
                            "`.env`、密钥、数据库文件会被自动跳过。"
                        )

                        folder_path_input = (
                            gr.Textbox(
                                label="本地文件夹路径",
                                placeholder=(
                                    r"D:\projects\demo"
                                ),
                                elem_id="folder-path-input",
                            )
                        )

                        with gr.Row():
                            scan_folder_button = (
                                gr.Button(
                                    "扫描"
                                )
                            )
                            import_folder_button = (
                                gr.Button(
                                    "导入到工作区",
                                    variant="primary",
                                )
                            )

                        folder_scan_status = (
                            gr.Markdown(
                                value="",
                                elem_id="folder-scan-status",
                            )
                        )

                        refresh_files_button = (
                            gr.Button(
                                "刷新工作区"
                            )
                        )

                        file_selector = (
                            gr.Dropdown(
                                choices=(
                                    workspace_files()
                                ),
                                label="文件",
                            )
                        )

                        download_file_button = gr.Button("准备下载所选文件")
                        workspace_download = gr.File(label="下载成果", interactive=False)

                        file_preview = (
                            gr.Code(
                                value="",
                                label="预览",
                                language=None,
                                lines=18,
                                interactive=False,
                            )
                        )

                with gr.Tab('能力'):
                    gr.Markdown("**执行能力** · 联网 · 文件 · 代码 · 终端 · MCP\n\n终端使用当前 Windows 用户权限，并非沙箱。执行前会显示命令供你审批。")

                    refresh_tools_button = (
                        gr.Button(
                            "刷新工具列表"
                        )
                    )

                    tools_box = (
                        gr.Markdown(
                            value=(
                                tools_overview_markdown()
                            ),
                            elem_id="tools-box",
                        )
                    )

                    with gr.Accordion('上下文与记忆', open=False):
                        refresh_context_button = (
                            gr.Button(
                                "刷新上下文状态"
                            )
                        )

                        context_box = (
                            gr.Markdown(
                                value=(
                                    context_markdown()
                                ),
                                elem_id="context-box",
                            )
                        )

                    with gr.Accordion('开发者与审计', open=False):
                        with gr.Column(
                            elem_id="developer-panel",
                        ):

                            session_box = (
                                gr.Textbox(
                                    value=(
                                        service
                                        .get_session_id()
                                    ),
                                    label="会话 ID",
                                    interactive=False,
                                )
                            )

                            task_box = (
                                gr.Textbox(
                                    value="—",
                                    label="任务 ID",
                                    interactive=False,
                                )
                            )

                            refresh_audit_button = (
                                gr.Button(
                                    "刷新审计"
                                )
                            )

                            audit_table = (
                                gr.Dataframe(
                                    headers=(
                                        AUDIT_HEADERS
                                    ),
                                    datatype=[
                                        "str",
                                        "str",
                                        "str",
                                        "str",
                                        "str",
                                    ],
                                    value=(
                                        load_audit_rows()
                                    ),
                                    interactive=False,
                                    wrap=True,
                                    max_height=420,
                                )
                            )

                with gr.Tab('插件'):
                    gr.Markdown(
                        "**插件** · 单文件 `.py` 扩展\n\n"
                        "插件是本机 Python 代码，启用后拥有与小智相同的权限。"
                        "只启用你信得过的来源。",
                        elem_id="plugin-intro",
                    )

                    plugin_summary_box = (
                        gr.HTML(
                            plugin_summary_markdown(),
                            elem_id="plugin-summary",
                        )
                    )

                    plugin_selector = (
                        gr.Dropdown(
                            choices=plugin_choices(),
                            label="已安装插件",
                            interactive=True,
                        )
                    )

                    plugin_detail = (
                        gr.Markdown(
                            "在上方选择一个插件查看详情。",
                            elem_id="plugin-detail",
                        )
                    )

                    with gr.Row():

                        enable_plugin_button = (
                            gr.Button(
                                "启用",
                                variant="primary",
                            )
                        )

                        disable_plugin_button = (
                            gr.Button(
                                "停用"
                            )
                        )

                        uninstall_plugin_button = (
                            gr.Button(
                                "卸载",
                                variant="stop",
                                elem_id="plugin-uninstall-button",
                            )
                        )

                    refresh_plugins_button = (
                        gr.Button(
                            "刷新插件列表"
                        )
                    )

                    plugin_table = (
                        gr.Dataframe(
                            headers=PLUGIN_TABLE_HEADERS,
                            datatype=[
                                "str",
                                "str",
                                "str",
                                "str",
                                "str",
                            ],
                            value=plugin_rows(),
                            interactive=False,
                            wrap=True,
                            max_height=300,
                            elem_id="plugin-table",
                        )
                    )

                    gr.Markdown(
                        "---\n\n"
                        "**安装插件**\n\n"
                        "选择一个本地 `.py` 文件后点击安装。"
                        "安装后默认不启用。"
                    )

                    plugin_upload = (
                        gr.File(
                            label="插件文件",
                            type="filepath",
                            file_count="single",
                        )
                    )

                    install_plugin_button = (
                        gr.Button(
                            "安装插件",
                            variant="primary",
                        )
                    )

                    plugin_status = (
                        gr.Markdown(
                            value="",
                            elem_id="plugin-status",
                        )
                    )

                    with gr.Accordion(
                        '怎么写一个插件',
                        open=False,
                    ):

                        gr.Markdown(
                            plugin_help_markdown()
                        )

                with gr.Tab('设置'):
                                        with gr.Column(
                                            elem_id="settings-panel",
                                        ):

                                            current_settings = (
                                                _settings_snapshot()
                                            )

                                            current_general = (
                                                current_settings.get(
                                                    "general",
                                                    {},
                                                )
                                                or {}
                                            )

                                            current_model = (
                                                current_settings.get(
                                                    "model",
                                                    {},
                                                )
                                                or {}
                                            )

                                            current_agent = (
                                                current_settings.get(
                                                    "agent",
                                                    {},
                                                )
                                                or {}
                                            )

                                            current_web = (
                                                current_settings.get(
                                                    "web",
                                                    {},
                                                )
                                                or {}
                                            )

                                            with gr.Tabs():

                                                # -----------------------------
                                                # 模型
                                                # -----------------------------

                                                with gr.Tab(
                                                    "模型"
                                                ):

                                                    gr.Markdown(
                                                        """
                    ### 模型与接口

                    当前使用 OpenAI 兼容接口。
                    填好接口地址、模型名称和 API Key 后点「保存模型配置」，
                    **立即生效，不需要重启**；可以先用「测试连接」确认可用。
                    """
                                                    )

                                                    provider_name_setting = (
                                                        gr.Textbox(
                                                            value=str(
                                                                current_model.get(
                                                                    "provider_name",
                                                                    "OpenAI 兼容接口",
                                                                )
                                                            ),
                                                            label="提供方名称",
                                                        )
                                                    )

                                                    base_url_setting = (
                                                        gr.Textbox(
                                                            value=str(
                                                                current_model.get(
                                                                    "base_url",
                                                                    "",
                                                                )
                                                            ),
                                                            label="接口地址",
                                                            placeholder=(
                                                                "例如：https://example.com/v1"
                                                            ),
                                                        )
                                                    )

                                                    model_name_setting = (
                                                        gr.Textbox(
                                                            value=str(
                                                                current_model.get(
                                                                    "model_name",
                                                                    "",
                                                                )
                                                            ),
                                                            label="模型名称",
                                                        )
                                                    )

                                                    api_key_setting = (
                                                        gr.Textbox(
                                                            value="",
                                                            label="API Key",
                                                            type="password",
                                                            placeholder=(
                                                                "留空表示不修改当前密钥"
                                                            ),
                                                        )
                                                    )

                                                    api_key_status_box = (
                                                        gr.Markdown(
                                                            value=(
                                                                api_key_status_markdown()
                                                            ),
                                                            elem_classes=[
                                                                "settings-card",
                                                            ],
                                                        )
                                                    )

                                                    with gr.Row():

                                                        save_model_button = (
                                                            gr.Button(
                                                                "保存模型配置",
                                                                variant="primary",
                                                            )
                                                        )

                                                        test_model_button = (
                                                            gr.Button(
                                                                "测试连接"
                                                            )
                                                        )

                                                    with gr.Row():

                                                        migrate_api_key_button = (
                                                            gr.Button(
                                                                "迁移 .env 密钥"
                                                            )
                                                        )

                                                        delete_api_key_button = (
                                                            gr.Button(
                                                                "删除安全存储密钥",
                                                                variant="stop",
                                                            )
                                                        )

                                                    model_settings_status = (
                                                        gr.Markdown(
                                                            ""
                                                        )
                                                    )

                                                # -----------------------------
                                                # 常规
                                                # -----------------------------

                                                with gr.Tab("外观", render_children=True):
                                                    gr.HTML(
                                                        APPEARANCE_HTML,
                                                        css_template=APPEARANCE_CSS,
                                                        js_on_load="""
                                                        const attach = () => window.xiaozhiAppearance.attach(element);
                                                        if (window.xiaozhiAppearance) attach();
                                                        else document.addEventListener('xiaozhi-appearance-ready', attach, {once: true});
                                                        """,
                                                        elem_id="appearance-settings",
                                                    )

                                                with gr.Tab(
                                                    "常规"
                                                ):

                                                    gr.Markdown(
                                                        """
                    ### 常规

                    界面语言固定为**简体中文**。
                    """
                                                    )

                                                    open_browser_setting = (
                                                        gr.Checkbox(
                                                            value=bool(
                                                                current_general.get(
                                                                    "open_browser",
                                                                    True,
                                                                )
                                                            ),
                                                            label=(
                                                                "启动小智时自动打开浏览器"
                                                            ),
                                                        )
                                                    )

                                                    save_general_button = (
                                                        gr.Button(
                                                            "保存常规设置",
                                                            variant="primary",
                                                        )
                                                    )

                                                    general_settings_status = (
                                                        gr.Markdown(
                                                            ""
                                                        )
                                                    )

                                                # -----------------------------
                                                # 智能体
                                                # -----------------------------

                                                with gr.Tab(
                                                    "智能体"
                                                ):

                                                    gr.Markdown(
                                                        """
                    ### 智能体运行

                    最大轮数限制一次任务中模型与工具之间可以进行多少轮交互。
                    数值过高会增加成本与失控风险。
                    """
                                                    )

                                                    max_turns_setting = (
                                                        gr.Slider(
                                                            minimum=3,
                                                            maximum=40,
                                                            step=1,
                                                            value=int(
                                                                current_agent.get(
                                                                    "max_turns",
                                                                    10,
                                                                )
                                                            ),
                                                            label="单任务最大轮数",
                                                        )
                                                    )

                                                    save_agent_button = (
                                                        gr.Button(
                                                            "保存智能体设置",
                                                            variant="primary",
                                                        )
                                                    )

                                                    agent_settings_status = (
                                                        gr.Markdown(
                                                            ""
                                                        )
                                                    )

                                                # -----------------------------
                                                # 联网
                                                # -----------------------------

                                                with gr.Tab(
                                                    "联网"
                                                ):

                                                    gr.Markdown(
                                                        """
                    ### 联网行为

                    控制普通搜索、深度研究的搜索预算，
                    以及读取网页正文时的超时限制。
                    """
                                                    )

                                                    normal_budget_setting = (
                                                        gr.Slider(
                                                            minimum=1,
                                                            maximum=12,
                                                            step=1,
                                                            value=int(
                                                                current_web.get(
                                                                    "normal_search_budget",
                                                                    4,
                                                                )
                                                            ),
                                                            label="普通任务搜索预算",
                                                        )
                                                    )

                                                    research_budget_setting = (
                                                        gr.Slider(
                                                            minimum=1,
                                                            maximum=20,
                                                            step=1,
                                                            value=int(
                                                                current_web.get(
                                                                    "research_search_budget",
                                                                    6,
                                                                )
                                                            ),
                                                            label="深度研究搜索预算",
                                                        )
                                                    )

                                                    fetch_timeout_setting = (
                                                        gr.Slider(
                                                            minimum=5,
                                                            maximum=60,
                                                            step=1,
                                                            value=int(
                                                                current_web.get(
                                                                    "fetch_timeout_seconds",
                                                                    15,
                                                                )
                                                            ),
                                                            label="网页读取超时（秒）",
                                                        )
                                                    )

                                                    save_web_button = (
                                                        gr.Button(
                                                            "保存联网设置",
                                                            variant="primary",
                                                        )
                                                    )

                                                    web_settings_status = (
                                                        gr.Markdown(
                                                            ""
                                                        )
                                                    )

                                                # -----------------------------
                                                # MCP
                                                # -----------------------------

                                                with gr.Tab(
                                                    "MCP"
                                                ):

                                                    gr.Markdown(
                                                        """
                    ### MCP 管理

                    管理本地 stdio 与 Streamable HTTP MCP 服务器。

                    **安全默认值：**
                    新配置默认停用，并且 MCP 工具默认每次都需要人工批准。
                    只有点击“测试连接”时，当前页面才会主动连接或启动服务器。
                    """
                                                    )

                                                    mcp_dependency_box = (
                                                        gr.Markdown(
                                                            value=(
                                                                mcp_dependency_markdown()
                                                            ),
                                                            elem_classes=[
                                                                "settings-card",
                                                            ],
                                                        )
                                                    )

                                                    mcp_server_table = (
                                                        gr.Dataframe(
                                                            headers=(
                                                                MCP_TABLE_HEADERS
                                                            ),
                                                            datatype=[
                                                                "str",
                                                                "str",
                                                                "str",
                                                                "str",
                                                                "str",
                                                                "str",
                                                            ],
                                                            value=(
                                                                mcp_server_rows()
                                                            ),
                                                            interactive=False,
                                                            wrap=True,
                                                            max_height=260,
                                                        )
                                                    )

                                                    with gr.Row():

                                                        mcp_refresh_button = (
                                                            gr.Button(
                                                                "刷新列表"
                                                            )
                                                        )

                                                        mcp_new_button = (
                                                            gr.Button(
                                                                "新建配置"
                                                            )
                                                        )

                                                    mcp_server_selector = (
                                                        gr.Dropdown(
                                                            choices=(
                                                                mcp_server_choices()
                                                            ),
                                                            label="选择服务器",
                                                            interactive=True,
                                                        )
                                                    )

                                                    mcp_server_id_state = (
                                                        gr.State("")
                                                    )

                                                    mcp_name = (
                                                        gr.Textbox(
                                                            label="名称",
                                                            placeholder=(
                                                                "例如：本地文件系统"
                                                            ),
                                                        )
                                                    )

                                                    mcp_transport = (
                                                        gr.Dropdown(
                                                            choices=[
                                                                (
                                                                    "本地 stdio",
                                                                    "stdio",
                                                                ),
                                                                (
                                                                    "Streamable HTTP",
                                                                    "streamable_http",
                                                                ),
                                                            ],
                                                            value="stdio",
                                                            label="传输方式",
                                                        )
                                                    )

                                                    with gr.Row():

                                                        mcp_enabled = (
                                                            gr.Checkbox(
                                                                value=False,
                                                                label="启用",
                                                            )
                                                        )

                                                        mcp_cache_tools = (
                                                            gr.Checkbox(
                                                                value=True,
                                                                label="缓存工具列表",
                                                            )
                                                        )

                                                    mcp_approval = (
                                                        gr.Dropdown(
                                                            choices=[
                                                                (
                                                                    "智能：只读自动，写入询问",
                                                                    "smart",
                                                                ),
                                                                (
                                                                    "每次调用都需要批准",
                                                                    "always",
                                                                ),
                                                                (
                                                                    "全部允许自动执行",
                                                                    "never",
                                                                ),
                                                            ],
                                                            value="smart",
                                                            label="工具批准策略",
                                                        )
                                                    )

                                                    mcp_tool_prefix = (
                                                        gr.Textbox(
                                                            label=(
                                                                "工具名前缀（英文，可选）"
                                                            ),
                                                            placeholder=(
                                                                "例如：filesystem"
                                                            ),
                                                            info=(
                                                                "中文名称无法作为工具名，"
                                                                "留空会自动生成。"
                                                            ),
                                                        )
                                                    )

                                                    mcp_timeout = (
                                                        gr.Slider(
                                                            minimum=3,
                                                            maximum=120,
                                                            step=1,
                                                            value=10,
                                                            label="连接/会话超时（秒）",
                                                        )
                                                    )

                                                    with gr.Accordion(
                                                        "本地 stdio 配置",
                                                        open=True,
                                                    ):

                                                        mcp_stdio_command = (
                                                            gr.Textbox(
                                                                label="启动命令",
                                                                placeholder=(
                                                                    "例如：npx 或 python"
                                                                ),
                                                            )
                                                        )

                                                        mcp_stdio_args = (
                                                            gr.Textbox(
                                                                label=(
                                                                    "启动参数（一行一个）"
                                                                ),
                                                                lines=5,
                                                                placeholder=(
                                                                    "-y\n"
                                                                    "@modelcontextprotocol/server-filesystem\n"
                                                                    "D:\\\\myagent-clean\\\\workspace"
                                                                ),
                                                            )
                                                        )

                                                        mcp_stdio_cwd = (
                                                            gr.Textbox(
                                                                label="工作目录（可选）",
                                                            )
                                                        )

                                                        mcp_stdio_env = (
                                                            gr.Code(
                                                                value="{}",
                                                                label=(
                                                                    "普通环境变量 JSON"
                                                                ),
                                                                language="json",
                                                                lines=5,
                                                            )
                                                        )

                                                        mcp_secret_env = (
                                                            gr.Textbox(
                                                                value="",
                                                                label=(
                                                                    "安全环境变量 JSON"
                                                                ),
                                                                type="password",
                                                                placeholder=(
                                                                    '例如：{"TOKEN":"..."}；'
                                                                    "留空表示保持原值"
                                                                ),
                                                            )
                                                        )

                                                    with gr.Accordion(
                                                        "Streamable HTTP 配置",
                                                        open=False,
                                                    ):

                                                        mcp_http_url = (
                                                            gr.Textbox(
                                                                label="MCP URL",
                                                                placeholder=(
                                                                    "https://example.com/mcp"
                                                                ),
                                                            )
                                                        )

                                                        mcp_http_headers = (
                                                            gr.Code(
                                                                value="{}",
                                                                label=(
                                                                    "普通请求头 JSON"
                                                                ),
                                                                language="json",
                                                                lines=5,
                                                            )
                                                        )

                                                        mcp_secret_headers = (
                                                            gr.Textbox(
                                                                value="",
                                                                label=(
                                                                    "安全请求头 JSON"
                                                                ),
                                                                type="password",
                                                                placeholder=(
                                                                    '{"Authorization":"Bearer ..."}；'
                                                                    "留空表示保持原值"
                                                                ),
                                                            )
                                                        )

                                                    with gr.Row():

                                                        mcp_save_button = (
                                                            gr.Button(
                                                                "保存配置",
                                                                variant="primary",
                                                            )
                                                        )

                                                        mcp_test_button = (
                                                            gr.Button(
                                                                "测试连接"
                                                            )
                                                        )

                                                        mcp_delete_button = (
                                                            gr.Button(
                                                                "删除配置",
                                                                variant="stop",
                                                            )
                                                        )

                                                    mcp_status_box = (
                                                        gr.Markdown(
                                                            ""
                                                        )
                                                    )

                                                    mcp_tools_table = (
                                                        gr.Dataframe(
                                                            headers=[
                                                                "工具",
                                                                "说明",
                                                            ],
                                                            datatype=[
                                                                "str",
                                                                "str",
                                                            ],
                                                            value=[],
                                                            interactive=False,
                                                            wrap=True,
                                                            max_height=320,
                                                        )
                                                    )

                                                # -----------------------------
                                                # 高级
                                                # -----------------------------

                                                with gr.Tab(
                                                    "高级"
                                                ):

                                                    settings_info_box = (
                                                        gr.Markdown(
                                                            value=(
                                                                settings_info_markdown()
                                                            ),
                                                            elem_classes=[
                                                                "settings-card",
                                                            ],
                                                        )
                                                    )

# ============================================================
# Event Wiring
# ============================================================

with demo:

    for action_button in [
        new_session_button, clear_chat_button, rename_conversation_button,
        delete_conversation_button, refresh_conversations_button, export_button,
        import_files_button, download_file_button, refresh_files_button,
        refresh_plan_button, refresh_tools_button, refresh_context_button,
        refresh_audit_button, save_general_button, save_model_button, test_model_button,
        migrate_api_key_button, delete_api_key_button, save_agent_button, save_web_button,
        mcp_refresh_button, mcp_new_button, mcp_save_button, mcp_test_button, mcp_delete_button,
    ]:
        action_button.elem_classes = [*(action_button.elem_classes or []), "action-feedback"]

    CONTEXT_OUTPUTS = [
        context_badge,
        context_box,
    ]

    SEND_OUTPUTS = [
        chatbot,
        status_box,
        session_box,
        task_box,
        approval_box,
        approve_button,
        reject_button,
        send_button,
        new_session_button,
        trace_table,
        trace_state,
        sources_box,
        stop_button,
        message_box,
    ]

    send_event = (
        send_button.click(
            fn=send_task,
            inputs=[
                message_box,
                chatbot,
                trace_state,
            ],
            outputs=SEND_OUTPUTS,
            # 任务流独占一个并发组：
            # 组内串行（同一时刻只允许一个 Task 在跑，
            # AgentService 本来就是单任务状态机），
            # 但不再占用全局并发额度——
            # 否则任务流式期间刷新文件列表、读审计日志、
            # 保存设置全都被排队卡死，表现为"界面假死"。
            concurrency_id="agent-stream",
            concurrency_limit=1,
        )
    )

    send_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    send_event.then(
        fn=runtime_status_markdown,
        inputs=[],
        outputs=[
            runtime_status,
        ],
    )

    APPROVAL_OUTPUTS = [
        chatbot,
        status_box,
        session_box,
        task_box,
        approval_box,
        approve_button,
        reject_button,
        send_button,
        new_session_button,
        trace_table,
        trace_state,
        sources_box,
        stop_button,
    ]

    approve_event = (
        approve_button.click(
            fn=approve_task,
            inputs=[
                chatbot,
                trace_state,
                auto_approve_checkbox,
            ],
            outputs=APPROVAL_OUTPUTS,
            # 与 send_event 同组：审批恢复是同一个任务流的延续，
            # 必须串行，但也必须共用同一个槽位而不是全局锁。
            concurrency_id="agent-stream",
            concurrency_limit=1,
        )
    )

    # 每次批准后把开关复位，
    # 避免下一次不相关的审批误用上一次的授权。
    approve_event.then(
        fn=lambda: False,
        outputs=[
            auto_approve_checkbox,
        ],
    )

    approve_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    approve_event.then(
        fn=runtime_status_markdown,
        inputs=[],
        outputs=[
            runtime_status,
        ],
    )

    reject_event = (
        reject_button.click(
            fn=reject_task,
            inputs=[
                chatbot,
                trace_state,
            ],
            outputs=APPROVAL_OUTPUTS,
            concurrency_id="agent-stream",
            concurrency_limit=1,
        )
    )

    reject_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    reject_event.then(
        fn=runtime_status_markdown,
        inputs=[],
        outputs=[
            runtime_status,
        ],
    )

    demo.load(fn=restore_view_ui, outputs=[
        chatbot, session_box, task_box, conversation_selector, conversation_title,
        approval_box, approve_button, reject_button, send_button, new_session_button,
        stop_button, status_box, runtime_status, plan_box, context_badge,
    ])

    # 页面打开后后台预热 MCP 连接池。
    # stdio Server（npx）冷启动要十几秒，
    # 提前连好，用户发第一句话时就不必干等。
    demo.load(fn=warmup_mcp_ui, concurrency_id="mcp-warmup")

    # ========================================================
    # 停止任务
    #
    # 使用独立 concurrency_id，
    # 否则会被正在运行的 send_event 阻塞。
    # ========================================================

    stop_button.click(
        fn=stop_task,
        inputs=[],
        outputs=[
            status_box,
            stop_button,
        ],
        concurrency_id="agent-control",
        concurrency_limit=4,
    )

    # ========================================================
    # 快捷任务
    # ========================================================

    for _button, (
        _title,
        _prompt,
    ) in zip(
        quick_buttons,
        QUICK_PROMPTS[:4],
    ):

        _button.click(
            fn=(
                lambda _p=_prompt: (gr.Info("任务已填入输入框，点击发送即可开始。"), _p)[1]
            ),
            inputs=[],
            outputs=[
                message_box,
            ],
        )

    # ========================================================
    # 工具总览
    # ========================================================

    refresh_tools_button.click(
        fn=action_feedback(tools_overview_markdown, '能力列表已刷新。'),
        inputs=[],
        outputs=[
            tools_box,
        ],
    )

    # ========================================================
    # 导出对话
    # ========================================================

    export_button.click(
        fn=export_conversation_markdown,
        inputs=[],
        outputs=[
            export_file,
            export_status,
        ],
    )

    open_workspace_button.click(
        fn=open_workspace_folder,
        inputs=[],
        outputs=[
            export_status,
        ],
    )

    # ========================================================
    # 输入框「+」菜单 / 拖拽上传
    # ========================================================

    composer_upload.upload(
        fn=import_attachments_ui,
        inputs=[
            composer_upload,
            message_box,
        ],
        outputs=[
            message_box,
            file_selector,
            attachment_status,
            composer_upload,
        ],
    ).then(fn=workspace_summary_markdown, outputs=[workspace_path])

    folder_quick_import.click(
        fn=import_folder_ui,
        inputs=[
            folder_quick_path,
            message_box,
        ],
        outputs=[
            message_box,
            file_selector,
            folder_scan_status,
        ],
    ).then(fn=workspace_summary_markdown, outputs=[workspace_path])

    # ========================================================
    # 插件
    # ========================================================

    PLUGIN_OUTPUTS = [
        plugin_status,
        plugin_selector,
        plugin_table,
        plugin_summary_box,
        plugin_detail,
    ]

    plugin_selector.change(
        fn=select_plugin_ui,
        inputs=[
            plugin_selector,
        ],
        outputs=[
            plugin_detail,
        ],
    )

    refresh_plugins_button.click(
        fn=action_feedback(
            refresh_plugins_ui,
            "插件列表已刷新。",
        ),
        inputs=[],
        outputs=[
            plugin_selector,
            plugin_table,
            plugin_summary_box,
        ],
    )

    refresh_plugins_button.click(
        fn=plugin_summary_markdown,
        inputs=[],
        outputs=[
            plugin_summary_left,
        ],
    )

    enable_plugin_button.click(
        fn=enable_plugin_ui,
        inputs=[
            plugin_selector,
        ],
        outputs=PLUGIN_OUTPUTS,
    )

    enable_plugin_button.click(
        fn=plugin_summary_markdown,
        inputs=[],
        outputs=[
            plugin_summary_left,
        ],
    )

    disable_plugin_button.click(
        fn=disable_plugin_ui,
        inputs=[
            plugin_selector,
        ],
        outputs=PLUGIN_OUTPUTS,
    )

    disable_plugin_button.click(
        fn=plugin_summary_markdown,
        inputs=[],
        outputs=[
            plugin_summary_left,
        ],
    )

    uninstall_plugin_button.click(
        fn=uninstall_plugin_ui,
        inputs=[
            plugin_selector,
        ],
        outputs=PLUGIN_OUTPUTS,
    )

    uninstall_plugin_button.click(
        fn=plugin_summary_markdown,
        inputs=[],
        outputs=[
            plugin_summary_left,
        ],
    )

    install_plugin_button.click(
        fn=install_plugin_ui,
        inputs=[
            plugin_upload,
        ],
        outputs=PLUGIN_OUTPUTS,
    )

    install_plugin_button.click(
        fn=plugin_summary_markdown,
        inputs=[],
        outputs=[
            plugin_summary_left,
        ],
    )

    manage_plugin_button.click(
        fn=plugin_summary_markdown,
        inputs=[],
        outputs=[
            plugin_summary_left,
        ],
        js="() => window.xiaozhiOpenTab('插件')",
    )

    open_model_settings_button.click(
        fn=None,
        js="() => window.xiaozhiOpenModelSettings()",
    )

    new_session_event = (
        new_session_button.click(
            fn=create_session,
            inputs=[],
            # 新建会话会改写全局单点的 current_session，
            # 必须与其它会话操作串行。
            concurrency_id="session-control",
            concurrency_limit=1,
            outputs=[
                chatbot,
                session_box,
                task_box,
                status_box,
                approval_box,
                trace_table,
                trace_state,
                sources_box,
                approve_button,
                reject_button,
                conversation_selector,
                conversation_title,
                delete_confirm,
            ],
        )
    )

    new_session_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )
    new_session_event.then(fn=lambda: "", outputs=[message_box])
    new_session_event.then(fn=task_plan_markdown, outputs=[plan_box])

    clear_chat_button.click(
        fn=clear_chat,
        inputs=[],
        outputs=[
            chatbot,
            sources_box,
        ],
    )

    import_files_button.click(
        fn=import_attachments_ui,
        inputs=[attachment_files, message_box],
        outputs=[message_box, file_selector, attachment_status, attachment_files],
    ).then(fn=workspace_summary_markdown, outputs=[workspace_path])
    scan_folder_button.click(
        fn=scan_folder_ui,
        inputs=[folder_path_input],
        outputs=[folder_scan_status],
    )
    import_folder_button.click(
        fn=import_folder_ui,
        inputs=[folder_path_input, message_box],
        outputs=[message_box, file_selector, folder_scan_status],
    ).then(fn=workspace_summary_markdown, outputs=[workspace_path])
    download_file_button.click(fn=download_workspace_ui, inputs=[file_selector], outputs=[workspace_download])
    file_selector.change(fn=lambda: None, inputs=[], outputs=[workspace_download])
    refresh_plan_button.click(fn=action_feedback(task_plan_markdown, "任务计划已刷新。"), outputs=[plan_box])
    send_event.then(fn=task_plan_markdown, outputs=[plan_box])
    approve_event.then(fn=task_plan_markdown, outputs=[plan_box])
    send_event.then(fn=refresh_workspace, outputs=[file_selector])
    approve_event.then(fn=refresh_workspace, outputs=[file_selector])
    send_event.then(fn=workspace_summary_markdown, outputs=[workspace_path])
    approve_event.then(fn=workspace_summary_markdown, outputs=[workspace_path])
    send_event.then(fn=refresh_conversations_ui, outputs=[conversation_selector])
    approve_event.then(fn=refresh_conversations_ui, outputs=[conversation_selector])

    refresh_files_button.click(
        fn=action_feedback(refresh_workspace, '工作区文件列表已刷新。'),
        inputs=[],
        outputs=[
            file_selector,
        ],
    )

    file_selector.change(
        fn=preview_workspace_file,
        inputs=[
            file_selector,
        ],
        outputs=[
            file_preview,
        ],
    )

    refresh_audit_button.click(
        fn=action_feedback(refresh_audit, '审计记录已刷新。'),
        inputs=[],
        outputs=[
            audit_table,
        ],
    )

    refresh_context_button.click(
        fn=action_feedback(refresh_context_ui, "上下文状态已刷新。"),
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    save_general_button.click(
        fn=action_feedback(save_general_settings_ui, result_index=None),
        inputs=[
            open_browser_setting,
        ],
        outputs=[
            general_settings_status,
        ],
    )

    save_model_button.click(
        fn=action_feedback(save_model_settings_ui, result_index=0),
        inputs=[
            provider_name_setting,
            base_url_setting,
            model_name_setting,
            api_key_setting,
        ],
        outputs=[
            model_settings_status,
            api_key_status_box,
            api_key_setting,
            model_status_left,
        ],
    )

    test_model_button.click(
        fn=action_feedback(test_model_connection_ui, result_index=None),
        inputs=[
            base_url_setting,
            model_name_setting,
            api_key_setting,
        ],
        outputs=[
            model_settings_status,
        ],
    )

    migrate_api_key_button.click(
        fn=action_feedback(migrate_api_key_ui, result_index=0),
        inputs=[],
        outputs=[
            model_settings_status,
            api_key_status_box,
        ],
    )

    delete_api_key_button.click(
        fn=action_feedback(delete_api_key_ui, result_index=0),
        inputs=[],
        outputs=[
            model_settings_status,
            api_key_status_box,
        ],
    ).then(
        fn=model_status_markdown,
        outputs=[model_status_left],
    )

    save_agent_button.click(
        fn=action_feedback(save_agent_settings_ui, result_index=None),
        inputs=[
            max_turns_setting,
        ],
        outputs=[
            agent_settings_status,
        ],
    )

    save_web_button.click(
        fn=action_feedback(save_web_settings_ui, result_index=None),
        inputs=[
            normal_budget_setting,
            research_budget_setting,
            fetch_timeout_setting,
        ],
        outputs=[
            web_settings_status,
        ],
    )

    mcp_refresh_button.click(
        fn=action_feedback(mcp_refresh_ui, "MCP 服务列表已刷新。"),
        inputs=[],
        outputs=[
            mcp_server_selector,
            mcp_server_table,
            mcp_dependency_box,
        ],
    )

    mcp_new_button.click(
        fn=action_feedback(mcp_empty_form, "已打开新建表单，填写后点击保存配置。"),
        inputs=[],
        outputs=[
            mcp_name,
            mcp_transport,
            mcp_enabled,
            mcp_approval,
            mcp_tool_prefix,
            mcp_cache_tools,
            mcp_timeout,
            mcp_stdio_command,
            mcp_stdio_args,
            mcp_stdio_cwd,
            mcp_stdio_env,
            mcp_secret_env,
            mcp_http_url,
            mcp_http_headers,
            mcp_secret_headers,
            mcp_status_box,
        ],
    ).then(
        fn=lambda: "",
        inputs=[],
        outputs=[
            mcp_server_id_state,
        ],
    )

    mcp_server_selector.change(
        fn=mcp_load_ui,
        inputs=[
            mcp_server_selector,
        ],
        outputs=[
            mcp_name,
            mcp_transport,
            mcp_enabled,
            mcp_approval,
            mcp_tool_prefix,
            mcp_cache_tools,
            mcp_timeout,
            mcp_stdio_command,
            mcp_stdio_args,
            mcp_stdio_cwd,
            mcp_stdio_env,
            mcp_secret_env,
            mcp_http_url,
            mcp_http_headers,
            mcp_secret_headers,
            mcp_status_box,
        ],
    ).then(
        fn=lambda value: value or "",
        inputs=[
            mcp_server_selector,
        ],
        outputs=[
            mcp_server_id_state,
        ],
    )

    mcp_save_button.click(
        fn=action_feedback(mcp_save_ui, result_index=3),
        inputs=[
            mcp_server_id_state,
            mcp_name,
            mcp_transport,
            mcp_enabled,
            mcp_approval,
            mcp_tool_prefix,
            mcp_cache_tools,
            mcp_timeout,
            mcp_stdio_command,
            mcp_stdio_args,
            mcp_stdio_cwd,
            mcp_stdio_env,
            mcp_secret_env,
            mcp_http_url,
            mcp_http_headers,
            mcp_secret_headers,
        ],
        outputs=[
            mcp_server_selector,
            mcp_server_table,
            mcp_server_id_state,
            mcp_status_box,
            mcp_secret_env,
            mcp_secret_headers,
        ],
    ).then(
        # 配置变了：销毁 MCP 连接池，下次任务按新配置重连。
        fn=reset_mcp_pool_ui,
        inputs=[],
        outputs=[],
    )

    mcp_test_button.click(
        fn=action_feedback(mcp_test_ui, result_index=0),
        inputs=[
            mcp_server_id_state,
        ],
        outputs=[
            mcp_status_box,
            mcp_tools_table,
        ],
    )

    mcp_delete_button.click(
        fn=action_feedback(mcp_delete_ui, result_index=3),
        inputs=[
            mcp_server_id_state,
        ],
        outputs=[
            mcp_server_selector,
            mcp_server_table,
            mcp_server_id_state,
            mcp_status_box,
        ],
    ).then(
        # 配置变了：销毁 MCP 连接池，下次任务按新配置重连。
        fn=reset_mcp_pool_ui,
        inputs=[],
        outputs=[],
    )

    SESSION_SWITCH_OUTPUTS = [
        chatbot,
        session_box,
        task_box,
        status_box,
        approval_box,
        trace_table,
        trace_state,
        sources_box,
        conversation_selector,
        conversation_title,
        delete_confirm,
        approve_button,
        reject_button,
    ]

    switch_event = (
        conversation_selector.input(
            fn=switch_conversation_ui,
            inputs=[
                conversation_selector,
            ],
            outputs=SESSION_SWITCH_OUTPUTS,
        )
    )

    switch_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )
    switch_event.then(fn=task_plan_markdown, outputs=[plan_box])

    refresh_conversations_button.click(
        fn=action_feedback(refresh_conversations_ui, '会话列表已刷新。'),
        inputs=[],
        outputs=[
            conversation_selector,
        ],
    )

    rename_conversation_button.click(
        fn=action_feedback(rename_conversation_ui, result_index=2),
        inputs=[
            conversation_title,
        ],
        outputs=[
            conversation_selector,
            conversation_title,
            status_box,
        ],
    )

    delete_event = (
        delete_conversation_button.click(
            fn=action_feedback(delete_conversation_ui, result_index=3),
            inputs=[
                delete_confirm,
            ],
            outputs=SESSION_SWITCH_OUTPUTS,
        )
    )

    delete_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )
    delete_event.then(fn=task_plan_markdown, outputs=[plan_box])

# ============================================================
# Composer Keyboard UX
# ============================================================

KEYBOARD_JS = r"""
() => {
    if (window.__xiaozhiComposerKeyboardInstalled) {
        return;
    }

    window.__xiaozhiComposerKeyboardInstalled = true;

    document.addEventListener("click", (event) => {
        const button = event.target.closest("button.action-feedback");
        if (button && !button.disabled) {
            window.xiaozhiAppearance?.notify("已收到操作：" + button.textContent.trim());
        }
    }, true);

    // ---------------------------------------------------------
    // 在线字体（非阻塞；加载失败自动回退系统字体栈）
    // ---------------------------------------------------------

    (function () {
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = "https://fonts.googleapis.com/css2"
            + "?family=Plus+Jakarta+Sans:wght@400;500;600;700"
            + "&family=JetBrains+Mono:wght@400;500;600"
            + "&display=swap";
        document.head.appendChild(link);
    })();

    // ---------------------------------------------------------
    // 空状态：有消息就隐藏欢迎区
    // ---------------------------------------------------------

    function syncWelcome() {
        var chatbot = document.querySelector("#chatbot");
        var welcome = document.querySelector("#welcome");
        if (!chatbot || !welcome) { return; }

        var count = chatbot.querySelectorAll(
            ".message"
        ).length;

        welcome.style.display = count > 0 ? "none" : "";
    }

    // 流式输出时 DOM 变动很频繁，用 rAF 合并。
    var scheduled = false;

    function scheduleSync() {
        if (scheduled) { return; }
        scheduled = true;
        requestAnimationFrame(function () {
            scheduled = false;
            syncWelcome();
        });
    }

    new MutationObserver(scheduleSync).observe(
        document.body,
        {
            childList: true,
            subtree: true
        }
    );

    syncWelcome();
    window.addEventListener("load", syncWelcome);

    // ---------------------------------------------------------
    // 欢迎卡片：点击后写回输入框
    // ---------------------------------------------------------

    document.addEventListener(
        "click",
        (event) => {
            var card = event.target.closest(
                ".welcome-card"
            );

            if (!card) { return; }

            var prompt = card.getAttribute(
                "data-prompt"
            ) || "";

            var textarea = document.querySelector(
                "#message-composer textarea"
            );

            if (!textarea) { return; }

            textarea.value = prompt;
            textarea.dispatchEvent(
                new Event("input", { bubbles: true })
            );
            textarea.focus();
            window.xiaozhiAppearance?.notify("任务已填入输入框，按 Enter 或点击发送开始。");
        },
        true
    );

    document.addEventListener(
        "keydown",
        (event) => {
            const target = event.target;

            if (!(target instanceof HTMLTextAreaElement)) {
                return;
            }

            const composer = target.closest(
                "#message-composer"
            );

            if (!composer) {
                return;
            }

            // 中文/日文等输入法组合输入期间，不拦截 Enter。
            if (
                event.isComposing
                || event.keyCode === 229
            ) {
                return;
            }

            if (event.key !== "Enter") {
                return;
            }

            // Shift+Enter：由我们显式插入换行。
            // Gradio/Textbox 自己可能会拦截默认 Enter 行为，
            // 所以不能只依赖浏览器默认换行。
            if (event.shiftKey) {
                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation();

                const start = target.selectionStart ?? target.value.length;
                const end = target.selectionEnd ?? target.value.length;

                target.setRangeText(
                    "\n",
                    start,
                    end,
                    "end"
                );

                // 通知 Gradio 前端状态：textarea 内容已变化。
                target.dispatchEvent(
                    new InputEvent(
                        "input",
                        {
                            bubbles: true,
                            inputType: "insertLineBreak",
                            data: "\n"
                        }
                    )
                );

                return;
            }

            // 普通 Enter：阻止 textarea 换行并发送。
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();

            const button = document.querySelector(
                "#send-task-button button"
            ) || document.querySelector(
                "#send-task-button"
            );

            if (button) {
                button.click();
            }
        },
        true
    );
}
"""

# ------------------------------------------------------------
# 切 Tab 时把右栏滚动位置归零
#
# 右栏是 overflow-y:auto，在"能力"页滚到底后切到"进度"，
# 面板仍停在原来的滚动位置，看上去内容"跑没了"。
# ------------------------------------------------------------

TAB_SCROLL_JS = """
(function () {
    var reset = function (node) {
        while (node && node !== document.body) {
            if (node.scrollHeight > node.clientHeight + 4) {
                node.scrollTop = 0;
            }
            node = node.parentElement;
        }
    };

    document.addEventListener("click", function (event) {
        var btn = event.target.closest("button");
        if (!btn) { return; }
        if (btn.getAttribute("role") !== "tab"
            && !btn.closest(".tab-container")) { return; }

        var host = btn.closest("#right-panel")
            || btn.closest("#settings-panel");
        if (!host) { return; }

        setTimeout(function () { reset(host); }, 0);
    }, true);
})();
"""

# ------------------------------------------------------------
# 「+」菜单 + 拖拽上传 + Tab 跳转
# ------------------------------------------------------------

COMPOSER_MENU_JS = """
(function () {
    var gradioClick = function (selector) {
        var host = document.querySelector(selector);
        if (!host) { return false; }
        var btn = host.tagName === 'BUTTON' ? host : host.querySelector('button');
        if (!btn) { return false; }
        btn.click();
        return true;
    };

    // Gradio 6 有时把 elem_id 放在组件根节点，有时放在里面的
    // textarea/input 上——两种都要能写进去。
    var resolveField = function (selector) {
        var host = document.querySelector(selector);
        if (!host) { return null; }
        if (host.matches('textarea, input')) { return host; }
        return host.querySelector('textarea') || host.querySelector('input');
    };

    var setText = function (selector, value) {
        var field = resolveField(selector);
        if (!field) { return false; }
        field.value = value;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
    };

    var notify = function (text) {
        if (window.xiaozhiAppearance && window.xiaozhiAppearance.notify) {
            window.xiaozhiAppearance.notify(text);
        }
    };

    // 供按钮/菜单直接调用：按名字切到右栏某个 Tab
    window.xiaozhiOpenTab = function (name) {
        window.xiaozhiShell?.open();
        var host = document.querySelector('#right-panel');
        if (!host) { return false; }
        var tries = 0;
        function selectTab() {
            var buttons = host.querySelectorAll('button[role="tab"]');
            for (var i = 0; i < buttons.length; i++) {
                if ((buttons[i].textContent || '').trim() === name) {
                    buttons[i].click();
                    return;
                }
            }
            if (++tries < 12) { setTimeout(selectTab, 80); }
            else { notify('面板未能打开，请重新点击工作台。'); }
        }
        requestAnimationFrame(selectTab);
        return true;
    };

    // 直达「设置 → 模型」：先切右栏顶层 Tab，
    // 再轮询点开设置里嵌套的那层子 Tab（Gradio 是懒渲染的）。
    window.xiaozhiOpenModelSettings = function () {
        if (!window.xiaozhiOpenTab('设置')) { return false; }
        var tries = 0;
        var timer = setInterval(function () {
            tries += 1;
            var panel = document.querySelector('#settings-panel');
            var done = false;
            if (panel) {
                var subs = panel.querySelectorAll('.tab-container:not(.visually-hidden) button');
                for (var i = 0; i < subs.length; i++) {
                    if ((subs[i].textContent || '').trim() === '模型') {
                        subs[i].click();
                        done = true;
                        break;
                    }
                }
            }
            if (done || tries > 25) { clearInterval(timer); }
        }, 120);
        return true;
    };

    // ---------------------------------------------------------
    // 拖拽上传：整窗接管，松手即导入
    // ---------------------------------------------------------

    var overlay = document.createElement('div');
    overlay.id = 'xiaozhi-dropzone';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = '<div class="dz-card"><b>松手即导入工作区</b>'
        + '<span>路径会自动写进输入框，可直接让小智读取</span></div>';
    document.body.appendChild(overlay);

    var depth = 0;

    var hasFiles = function (event) {
        if (!event.dataTransfer) { return false; }
        var types = event.dataTransfer.types || [];
        return Array.prototype.indexOf.call(types, 'Files') !== -1;
    };

    var hideOverlay = function () {
        depth = 0;
        overlay.classList.remove('visible');
    };

    window.addEventListener('dragenter', function (event) {
        if (!hasFiles(event)) { return; }
        event.preventDefault();
        depth += 1;
        overlay.classList.add('visible');
    });

    window.addEventListener('dragover', function (event) {
        if (!hasFiles(event)) { return; }
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        overlay.classList.add('visible');
    });

    window.addEventListener('dragleave', function (event) {
        if (!hasFiles(event)) { return; }
        depth = Math.max(0, depth - 1);
        if (depth === 0) { hideOverlay(); }
    });

    window.addEventListener('drop', function (event) {
        if (!hasFiles(event)) { return; }
        // 不阻止的话浏览器会直接打开这个文件，页面被替换掉。
        event.preventDefault();
        hideOverlay();

        var files = event.dataTransfer.files;
        if (!files || !files.length) { return; }

        // 桌面拖进来的文件夹：浏览器只给一个空壳，拿不到真实路径，
        // 硬传上去会得到一个 0 字节的假文件，不如直接告诉用户走哪条路。
        var items = event.dataTransfer.items
            ? Array.prototype.slice.call(event.dataTransfer.items)
            : [];
        var droppedFolder = false;
        for (var index = 0; index < items.length; index += 1) {
            var entry = items[index].webkitGetAsEntry ? items[index].webkitGetAsEntry() : null;
            if (entry && entry.isDirectory) { droppedFolder = true; break; }
        }
        if (droppedFolder) {
            notify('拖入的是文件夹。浏览器读不到文件夹的绝对路径，请用输入框左侧的 ＋ → 导入本地文件夹。');
            return;
        }

        var input = document.querySelector('#composer-upload input[type=file]');
        if (!input) { notify('上传控件还没就绪，请刷新页面后重试。'); return; }

        try {
            input.files = files;
        } catch (error) {
            notify('这个浏览器不允许直接放入文件，请点输入框左侧的 ＋ 选择文件。');
            return;
        }

        input.dispatchEvent(new Event('change', { bubbles: true }));
        notify('已收到 ' + files.length + ' 个文件，正在导入工作区…');
    });

    // ---------------------------------------------------------
    // 「+」菜单
    // ---------------------------------------------------------

    var syncPlus = function (open) {
        var plus = document.querySelector('#composer-plus');
        if (!plus) { return; }
        plus.classList.toggle('menu-open', open);
        plus.setAttribute('aria-expanded', String(open));
    };

    var closeMenu = function () {
        var menu = document.querySelector('.composer-menu');
        if (menu) { menu.hidden = true; }
        syncPlus(false);
    };

    document.addEventListener('click', function (event) {
        var menu = document.querySelector('.composer-menu');
        if (!menu) { return; }

        if (event.target.closest('#composer-plus')) {
            event.preventDefault();
            menu.hidden = !menu.hidden;
            // 按钮自己也给反馈：展开时变强调色并转 45°（＋ → ×）
            syncPlus(!menu.hidden);
            if (!menu.hidden) {
                var folder = menu.querySelector('[data-folder]');
                if (folder) { folder.hidden = true; }
            }
            return;
        }

        var item = event.target.closest('.composer-menu [data-action]');
        if (item) {
            var action = item.dataset.action;

            if (action === 'upload') {
                var input = document.querySelector('#composer-upload input[type=file]');
                closeMenu();
                if (input) { input.click(); }
                else { notify('上传控件还没就绪，请刷新页面后重试。'); }
                return;
            }

            if (action === 'folder') {
                var row = menu.querySelector('[data-folder]');
                if (!row) { return; }
                row.hidden = !row.hidden;
                if (!row.hidden) { row.querySelector('input').focus(); }
                return;
            }

            if (action === 'folder-go') {
                var value = (menu.querySelector('[data-folder-input]').value || '').trim();
                if (!value) { notify('请先填写文件夹的绝对路径。'); return; }
                closeMenu();
                setText('#folder-quick-path', value);
                gradioClick('#quick-folder-btn');
                return;
            }

            if (action === 'clear') {
                closeMenu();
                gradioClick('#clear-display-button');
                return;
            }

            if (action === 'export') {
                closeMenu();
                gradioClick('#export-button');
                return;
            }

            if (action === 'open') {
                closeMenu();
                gradioClick('#open-workspace-button');
                return;
            }

            if (action === 'plugins') {
                closeMenu();
                if (!window.xiaozhiOpenTab('插件')) { notify('没有找到插件页，请在右栏手动切换。'); }
                return;
            }
            return;
        }

        // 点空白处收起
        if (!menu.hidden && !event.target.closest('#composer-menu')) {
            closeMenu();
        }
    }, true);

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { closeMenu(); }
    });
})();
"""

WORKBENCH_JS = (Path(__file__).parent / "assets" / "workbench.js").read_text(encoding="utf-8")
WORKBENCH_CSS = (Path(__file__).parent / "assets" / "workbench.css").read_text(encoding="utf-8")
WORKBENCH_JS = "{const style=document.createElement('style');style.textContent=" + json.dumps(WORKBENCH_CSS) + ";document.head.append(style);}" + WORKBENCH_JS
KEYBOARD_JS = KEYBOARD_JS.replace("() => {", "() => {\n" + APPEARANCE_JS + "\n" + WORKBENCH_JS + "\n", 1)
KEYBOARD_JS = KEYBOARD_JS.replace("() => {", "() => {\n" + TAB_SCROLL_JS + "\n", 1)
KEYBOARD_JS = KEYBOARD_JS.replace("() => {", "() => {\n" + COMPOSER_MENU_JS + "\n", 1)

# ============================================================
# Main
# 注意：只有 Main 退出 Blocks
# ============================================================

if __name__ == "__main__":

    # ============================================================
    # 并发额度
    #
    # 旧值 default_concurrency_limit=1 是全局单槽：
    # 只要有一条任务在流式输出，
    # 刷新文件列表 / 读审计日志 / 保存设置全部排队等它结束，
    # 界面表现就是"点了没反应、整页假死"。
    # 之前给停止按钮单独开 concurrency_id 只是给症状打补丁。
    #
    # 现在按职责分组：
    #   agent-stream    任务流（send / approve / reject），组内串行
    #   session-control 会话与设置写入，组内串行
    #   agent-control   停止等控制指令
    #   mcp-warmup      后台预热
    #   其余只读刷新  → 走默认额度，不再被任务流阻塞
    # ============================================================

    demo.queue(
        default_concurrency_limit=8
    )

    demo.launch(
        inbrowser=get_bool_setting(
            "general.open_browser",
            True,
        ),
        server_name="127.0.0.1",
        server_port=int(__import__("os").environ.get("GRADIO_SERVER_PORT", "7865")),
        max_file_size="20mb",
        show_error=True,
        theme=theme,
        css=CSS,
        js=KEYBOARD_JS,
    )
