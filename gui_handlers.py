"""GUI 事件处理函数。

这里全是纯逻辑：接收 Gradio 组件的输入、调用 service / settings /
MCP 管理器、返回给组件的数据。不接触界面布局，也不创建组件。

布局在 gui.py，两者通过「gui.py import 这里的函数」单向依赖。
"""

import html
import json
import os
import subprocess
import sys

from collections import Counter
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import gradio as gr
import httpx

from appearance import APPEARANCE_HTML, APPEARANCE_CSS, APPEARANCE_JS

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

from app_runtime import *  # noqa: F401,F403  service / 路径常量 / 名称映射
from app_runtime import service  # noqa: F401  显式声明，便于静态检查

import workspace_snapshots

# ============================================================
# 事件输出组的「长度」
#
# 原先 send_task / switch_conversation_ui 直接引用
# SEND_OUTPUTS、SESSION_SWITCH_OUTPUTS 这两个组件列表来构造
# "其余输出全部 skip" 的元组。拆分模块后这两个列表留在 gui.py
# 的 Blocks 里，handlers 引用它们会直接 NameError —— 而且只在
# 「任务忙 / 切换失败」这些分支才会走到，平时看不出来。
#
# handlers 真正需要的只是长度，不是组件本身。
# 改成数量常量，gui.py 构建完布局后断言长度一致。
# ============================================================

SEND_OUTPUT_COUNT = 14

SESSION_SWITCH_OUTPUT_COUNT = 13


def skip_tuple(count):
    """构造 count 个 gr.skip()，表示"这些输出都不更新"。"""
    return tuple(gr.skip() for _ in range(count))


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
def _session_time_label(raw):
    """把 '2026-09-23 17:30:12' 压成 '09-23 17:30:12'。

    保留到秒：同一分钟内新建几个会话是常有的事，
    只到分钟的话它们在列表里长得一模一样。
    """

    text = (raw or "").strip()

    if not text:
        return ""

    # 兼容带 T 的 ISO 写法
    text = text.replace("T", " ")

    date_part, _, time_part = (
        text.partition(" ")
    )

    date_bits = date_part.split("-")

    if len(date_bits) == 3:
        date_part = "-".join(
            date_bits[1:]
        )

    return (
        f"{date_part} "
        f"{time_part[:8]}"
    ).strip()


def _short_session_id(session_id):
    """从 'v2_4900050b-601a-...' 里取出可辨认的一小段。"""

    text = (session_id or "").strip()

    if not text:
        return ""

    # 形如 v2_<uuid>，去掉前缀只留 uuid 本体
    tail = text.split("_")[-1]

    tail = tail.replace("-", "")

    return tail[:6]


def conversation_choices():
    """
    生成对话历史 Dropdown 选项。

    显示：
        ● 会话名称 · 09-23 17:30:12 · 12 条

    实际传给回调：
        session_id

    带上时间和条数是为了让选项可辨认。
    原先一律是「标题 · N 项」，新建几个会话之后
    列表里全是「新对话 · 0 项」，根本分不清谁是谁。
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
            (
                item.get("title")
                or ""
            ).strip()
            or "未命名对话"
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
            else "  "
        )

        when = _session_time_label(
            item.get("updated_at")
            or item.get("created_at")
        )

        amount = (
            f"{count} 条"
            if count
            else "空"
        )

        label = (
            f"{prefix}{title}"
            f" · {when}"
            f" · {amount}"
        )

        choices.append(
            (
                label,
                session_id,
            )
        )

    # ====================================================
    # 兜底：仍然撞车就补上会话 ID 短前缀
    #
    # 同一秒内新建多个会话时，「标题 + 时间 + 条数」
    # 三个字段可能完全一样，列表里依旧分不清谁是谁。
    # ====================================================

    repeats = Counter(
        label
        for label, _
        in choices
    )

    if any(
        count > 1
        for count in repeats.values()
    ):

        choices = [
            (
                (
                    f"{label} · "
                    f"#{_short_session_id(session_id)}"
                )
                if repeats[label] > 1
                else label,
                session_id,
            )
            for label, session_id
            in choices
        ]

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
                or "未命名对话"
            )

    return "未命名对话"

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

    # 流式刷新间隔。每个 flush 都会把整个聊天历史作为完整
    # 消息列表重新传输并全量重渲 Markdown，0.04s（25fps）
    # 在长对话下会造成明显的卡顿与闪烁；0.12s 对肉眼流畅度
    # 几乎无感，但把重渲频率降到原来的三分之一。
    flush_interval = 0.12

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
        yield skip_tuple(SEND_OUTPUT_COUNT)
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
        "未命名对话",

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
        updates = list(skip_tuple(SESSION_SWITCH_OUTPUT_COUNT))
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
        updates = list(skip_tuple(SESSION_SWITCH_OUTPUT_COUNT))
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


def undo_last_change_ui():
    """撤销 Agent 最近一次对 workspace 文件的改动。

    只覆盖 write_file / edit_file；
    终端造成的变化不在快照里，这里撤不掉。
    """

    message = workspace_snapshots.undo_last()

    if "没有可撤销" in message:
        gr.Warning(message)
    else:
        gr.Info(message)

    return workspace_files()
