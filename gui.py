import json
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import gradio as gr
import httpx

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
    mcp_dependency_status,
    mcp_server_choices,
    mcp_server_rows,
    parse_args_text,
    parse_json_object_text,
    save_mcp_server,
    test_mcp_server,
)


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

def normalize_agent_text(
    value: Any,
) -> str:
    """
    修复少数第三方 OpenAI-compatible API
    在 Tool/HITL Streaming 后产生的
    “一个字一行”异常。

    正常文本不会修改。
    """

    if value is None:
        return ""

    text = str(value)

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if len(lines) < 12:
        return text

    tiny_lines = sum(
        1
        for line in lines
        if len(line) <= 4
    )

    ratio = (
        tiny_lines / len(lines)
        if lines
        else 0
    )

    if ratio >= 0.80:
        return "".join(
            lines
        )

    return text


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

        result.append(
            {
                "role": role,
                "content": content,
            }
        )

    return result


def ensure_assistant_message(
    history,
):

    if (
        not history
        or history[-1].get(
            "role"
        )
        != "assistant"
    ):

        history.append(
            {
                "role": "assistant",
                "content": "",
            }
        )

    return history


# ============================================================
# Trace
# ============================================================


def move_progress_message_to_trace(
    history,
    trace_state,
    time_text="",
):
    """
    如果模型在调用工具前输出了“我先搜索一下”“让我打开页面”等
    过程性文字，把它从聊天区移到执行过程。

    这样聊天区只保留用户消息与最终答案，更接近 Codex。
    """

    history = list(
        history or []
    )

    if not history:
        return (
            history,
            trace_state,
            False,
        )

    last = history[-1]

    if not isinstance(
        last,
        dict,
    ):
        return (
            history,
            trace_state,
            False,
        )

    if (
        last.get("role")
        != "assistant"
    ):
        return (
            history,
            trace_state,
            False,
        )

    content = str(
        last.get(
            "content",
            "",
        )
        or ""
    ).strip()

    if not content:
        return (
            history,
            trace_state,
            False,
        )

    # 只移动短的过程性消息，避免误删真正的长回答。
    # 真正最终答案一般明显更长，并且后面不会继续调用工具。
    if len(content) > 420:
        return (
            history,
            trace_state,
            False,
        )

    history.pop()

    trace_state = add_trace(
        trace_state,
        time_text=time_text,
        status="进度",
        tool="智能体",
        detail=content,
    )

    return (
        history,
        trace_state,
        True,
    )


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

        if not path.is_file():
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

        return candidate.read_text(
            encoding="utf-8"
        )

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
        "当前没有等待审批的操作。"
    )

    sources_text = (
        empty_sources_text()
    )

    assistant_streaming = False

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

            history = (
                ensure_assistant_message(
                    history
                )
            )

            delta = event.get(
                "delta",
                "",
            )

            if delta:

                history[-1][
                    "content"
                ] += delta

                assistant_streaming = (
                    True
                )

                status_text = (
                    "正在生成回答…"
                )

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

            (
                history,
                trace_state,
                moved_progress,
            ) = move_progress_message_to_trace(
                history,
                trace_state,
                event.get(
                    "time",
                    "",
                ),
            )

            if moved_progress:
                assistant_streaming = False

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

            approval_text = (
                "### 需要人工批准\n\n"
                f"**操作：** {tool_name}\n\n"
                "**参数：**\n\n"
                "```text\n"
                f"{arguments}\n"
                "```"
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

            # 没有 Streaming 文本时，用 final_output 兜底
            if (
                final_message
                and not assistant_streaming
            ):

                history.append(
                    {
                        "role": "assistant",
                        "content": (
                            final_message
                        ),
                    }
                )

            # 有 Streaming 时，修复可能存在的一字一行
            elif (
                history
                and history[-1].get(
                    "role"
                )
                == "assistant"
            ):

                history[-1][
                    "content"
                ] = (
                    normalize_agent_text(
                        history[-1].get(
                            "content",
                            "",
                        )
                    )
                )

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
                "当前没有等待审批的操作。"
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
                "当前没有等待审批的操作。"
            )

            force_flush = True

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
            }
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
            button_state(
                waiting_approval
            ),

            # 拒绝
            button_state(
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

    # --------------------------------------------------------
    # 空输入
    # --------------------------------------------------------

    if not message:

        yield (
            clone_history(
                history
            ),
            "请输入任务内容。",
            service.get_session_id(),
            current_task_text(),
            (
                "当前没有等待审批的操作。"
            ),
            button_state(False),
            button_state(False),
            button_state(True),
            button_state(True),
            trace_state or [],
            trace_state or [],
            empty_sources_text(),
            "",
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

    yield (
        history,
        "正在启动任务…",
        service.get_session_id(),
        current_task_text(),
        (
            "当前没有等待审批的操作。"
        ),
        button_state(False),
        button_state(False),
        button_state(False),
        button_state(False),
        trace_state or [],
        trace_state or [],
        empty_sources_text(),
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
            "",
        )


# ============================================================
# HITL 审批
# ============================================================

async def handle_approval(
    approved: bool,
    history,
    trace_state,
):

    async for update in (
        render_service_stream(
            service.stream_approval(
                approved=approved
            ),
            history,
            trace_state,
        )
    ):

        yield update


async def approve_task(
    history,
    trace_state,
):

    async for update in (
        handle_approval(
            True,
            history,
            trace_state,
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

    session_id = (
        service.new_session()
    )

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
        "当前没有等待审批的操作。",

        # trace_table
        [],

        # trace_state
        [],

        # sources_box
        empty_sources_text(),

        # approve_button
        button_state(False),

        # reject_button
        button_state(False),

        # conversation_selector
        conversation_dropdown_state(
            session_id
        ),

        # conversation_title
        "新对话",

        # delete_confirm
        False,
    )
async def switch_conversation_ui(
    session_id,
):
    """
    从左侧历史列表切换 Conversation。
    """

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
            "当前没有等待审批的操作。",
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            button_state(False),
            button_state(False),
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
            "当前没有等待审批的操作。",
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(
                new_session_id
            ),
            title,
            False,
            button_state(False),
            button_state(False),
        )

    except Exception as error:

        history = (
            await service
            .get_current_chat_history()
        )

        return (
            history,
            service.get_session_id(),
            "—",
            (
                "切换会话失败："
                f"{error}"
            ),
            "当前没有等待审批的操作。",
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            button_state(False),
            button_state(False),
        )


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
            "当前没有等待审批的操作。",
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            button_state(False),
            button_state(False),
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
            "当前没有等待审批的操作。",

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
            button_state(False),

            # Reject
            button_state(False),
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
            "当前没有等待审批的操作。",
            [],
            [],
            empty_sources_text(),
            conversation_dropdown_state(),
            current_conversation_title(),
            False,
            button_state(False),
            button_state(False),
        )


def refresh_conversations_ui():

    return conversation_dropdown_state(
        service.get_session_id()
    )

def clear_chat():

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
        )

    if not model_name:

        return (
            "保存失败：模型名称不能为空。",
            api_key_status_markdown(),
            "",
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
            )

    return (
        (
            "模型配置已保存。"
            "由于模型对象在程序启动时创建，"
            "Base URL、模型名称和 API Key "
            "将在重启小智后生效。"
        ),
        api_key_status_markdown(),
        "",
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
        "always",
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
        "新建 MCP 配置。默认停用，默认所有工具都需要批准。",
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
            "always",
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
# CSS
# ============================================================

CSS = r"""
:root {
    --agent-bg: #0b0b0d;
    --agent-panel: #111114;
    --agent-panel-2: #17171b;
    --agent-panel-3: #1d1d22;
    --agent-border: #2a2a31;
    --agent-border-soft: #202026;
    --agent-text: #f4f4f5;
    --agent-muted: #9b9ba3;
    --agent-hover: #202026;
    --agent-accent: #f1f1f2;
}

html,
body {
    background: var(--agent-bg) !important;
}

.gradio-container {
    max-width: 100% !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    background: var(--agent-bg) !important;
}

#topbar {
    min-height: 56px;
    padding: 9px 16px;
    border-bottom: 1px solid var(--agent-border);
    background: rgba(17, 17, 20, 0.96);
    align-items: center;
}

#brand-title {
    margin: 0 !important;
}

#brand-title h3,
#brand-title p {
    margin: 0 !important;
}

#context-badge {
    text-align: right;
    color: var(--agent-muted);
    padding-top: 6px;
}

#app-shell {
    min-height: calc(100vh - 58px);
    gap: 0 !important;
}

#left-panel {
    background: var(--agent-panel);
    border-right: 1px solid var(--agent-border);
    padding: 12px;
    min-height: calc(100vh - 58px);
}

#left-panel .sidebar-section {
    margin-top: 10px;
}

#left-panel button {
    width: 100%;
}

#center-panel {
    background: var(--agent-bg);
    padding: 10px 18px 14px 18px;
    min-height: calc(100vh - 58px);
}

#right-panel {
    background: var(--agent-panel);
    border-left: 1px solid var(--agent-border);
    padding: 10px;
    min-height: calc(100vh - 58px);
}

#chatbot {
    border: none !important;
    background: transparent !important;
}

#chatbot > div {
    background: transparent !important;
}

#composer {
    border: 1px solid var(--agent-border);
    border-radius: 16px;
    background: var(--agent-panel-2);
    padding: 8px;
    margin-top: 8px;
    box-shadow:
        0 8px 28px rgba(0, 0, 0, 0.18);
}

#composer textarea {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

#composer-hint {
    color: var(--agent-muted);
    font-size: 12px;
    margin: 3px 8px 0 8px;
}

#sources-box,
#context-box,
#activity-card {
    border: 1px solid var(--agent-border-soft);
    border-radius: 12px;
    background: var(--agent-panel-2);
    padding: 10px 12px;
}

#sources-box {
    max-height: 520px;
    overflow-y: auto;
}

#context-box {
    max-height: 520px;
    overflow-y: auto;
}

#right-panel .tabs {
    border: none !important;
}

#right-panel .tab-nav {
    gap: 4px !important;
}

#right-panel .tab-nav button {
    font-size: 12px !important;
    padding-left: 9px !important;
    padding-right: 9px !important;
}

#developer-panel,
#files-panel,
#settings-panel {
    padding-top: 4px;
}

#settings-panel .settings-card {
    border: 1px solid var(--agent-border-soft);
    border-radius: 12px;
    background: var(--agent-panel-2);
    padding: 10px 12px;
    margin-bottom: 10px;
}

#trace-table {
    margin-top: 8px;
}

button {
    border-radius: 9px !important;
}

footer {
    display: none !important;
}


/* 产品化细节 */
#left-panel .gradio-dropdown,
#right-panel .gradio-dropdown,
#right-panel .gradio-textbox,
#left-panel .gradio-textbox {
    border-radius: 10px !important;
}

#left-panel h3,
#right-panel h3 {
    letter-spacing: -0.01em;
}

#right-panel [role="tablist"] {
    position: sticky;
    top: 0;
    z-index: 5;
    background: var(--agent-panel);
    padding-bottom: 6px;
}

#right-panel [role="tab"] {
    min-width: auto !important;
    font-weight: 500 !important;
}

#chatbot {
    max-width: 980px;
    margin-left: auto;
    margin-right: auto;
}

#composer {
    max-width: 980px;
    margin-left: auto;
    margin-right: auto;
}

#composer-hint {
    max-width: 980px;
    margin-left: auto !important;
    margin-right: auto !important;
    text-align: right;
}

#activity-card {
    margin-bottom: 8px;
}

"""


# ============================================================
# UI
# ============================================================

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
                """
### 小智
智能体工作台
""",
                elem_id="brand-title",
            )

        with gr.Column(
            scale=3,
            min_width=240,
        ):

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

            new_session_button = (
                gr.Button(
                    "新建对话",
                    variant="primary",
                )
            )

            clear_chat_button = (
                gr.Button(
                    "清空当前界面"
                )
            )

            gr.Markdown(
                "### 对话"
            )

            conversation_selector = (
                gr.Dropdown(
                    choices=conversation_choices(),
                    value=service.get_session_id(),
                    label="对话历史",
                    interactive=True,
                    filterable=True,
                )
            )

            refresh_conversations_button = (
                gr.Button(
                    "刷新"
                )
            )

            with gr.Accordion(
                "管理当前对话",
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

            gr.Markdown(
                """
---
**工作区**

`D:\\myagent-clean`

当前工作区：myagent-clean
"""
            )

        # ====================================================
        # Center Chat
        # ====================================================

        with gr.Column(
            scale=7,
            min_width=520,
            elem_id="center-panel",
        ):

            chatbot = (
                gr.Chatbot(
                    value=[],
                    label="",
                    show_label=False,
                    height=700,
                    elem_id="chatbot",
                    placeholder=(
                        "输入你的任务。\n\n"
                        "小智可以调用工具、联网检索、读取网页、"
                        "访问工作区，并在敏感操作前请求批准。"
                    ),
                )
            )

            with gr.Row(
                elem_id="composer",
            ):

                message_box = (
                    gr.Textbox(
                        value="",
                        label="",
                        show_label=False,
                        placeholder=(
                            "输入任务……"
                        ),
                        lines=3,
                        max_lines=9,
                        scale=10,
                        elem_id="message-composer",
                    )
                )

                send_button = (
                    gr.Button(
                        "发送 ↑",
                        variant="primary",
                        scale=1,
                        elem_id="send-task-button",
                    )
                )

            gr.Markdown(
                "Enter 发送 · Shift+Enter 换行",
                elem_id="composer-hint",
            )

        # ====================================================
        # Right Product Panel
        # ====================================================

        with gr.Column(
            scale=3,
            min_width=330,
            elem_id="right-panel",
        ):

            with gr.Tabs():

                # --------------------------------------------
                # Activity
                # --------------------------------------------

                with gr.Tab(
                    "执行"
                ):

                    status_box = (
                        gr.Textbox(
                            value="就绪",
                            label="状态",
                            interactive=False,
                        )
                    )

                    approval_box = (
                        gr.Markdown(
                            (
                                "当前没有等待审批的操作。"
                            ),
                            elem_id="activity-card",
                        )
                    )

                    with gr.Row():

                        approve_button = (
                            gr.Button(
                                "批准",
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

                # --------------------------------------------
                # Sources
                # --------------------------------------------

                with gr.Tab(
                    "来源"
                ):

                    sources_box = (
                        gr.Markdown(
                            value=(
                                empty_sources_text()
                            ),
                            elem_id="sources-box",
                        )
                    )

                # --------------------------------------------
                # Context
                # --------------------------------------------

                with gr.Tab(
                    "上下文"
                ):

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

                # --------------------------------------------
                # Files
                # --------------------------------------------

                with gr.Tab(
                    "文件"
                ):

                    with gr.Column(
                        elem_id="files-panel",
                    ):

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

                        file_preview = (
                            gr.Code(
                                value="",
                                label="预览",
                                language=None,
                                lines=18,
                                interactive=False,
                            )
                        )

                # --------------------------------------------
                # 设置
                # --------------------------------------------

                with gr.Tab(
                    "设置"
                ):

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
                            # 常规
                            # -----------------------------

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
                            # 模型
                            # -----------------------------

                            with gr.Tab(
                                "模型"
                            ):

                                gr.Markdown(
                                    """
### 模型与接口

当前使用 OpenAI 兼容接口。
模型配置保存后需要**重启小智**，新的模型对象才会生效。
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
                                                "每次调用都需要批准",
                                                "always",
                                            ),
                                            (
                                                "允许自动执行",
                                                "never",
                                            ),
                                        ],
                                        value="always",
                                        label="工具批准策略",
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

                # --------------------------------------------
                # Developer
                # --------------------------------------------

                with gr.Tab(
                    "开发者"
                ):

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


# ============================================================
# Event Wiring
# ============================================================

with demo:

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
        )
    )

    send_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
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
    ]

    approve_event = (
        approve_button.click(
            fn=approve_task,
            inputs=[
                chatbot,
                trace_state,
            ],
            outputs=APPROVAL_OUTPUTS,
        )
    )

    approve_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    reject_event = (
        reject_button.click(
            fn=reject_task,
            inputs=[
                chatbot,
                trace_state,
            ],
            outputs=APPROVAL_OUTPUTS,
        )
    )

    reject_event.then(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    new_session_event = (
        new_session_button.click(
            fn=create_session,
            inputs=[],
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

    clear_chat_button.click(
        fn=clear_chat,
        inputs=[],
        outputs=[
            chatbot,
            sources_box,
        ],
    )

    refresh_files_button.click(
        fn=refresh_workspace,
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
        fn=refresh_audit,
        inputs=[],
        outputs=[
            audit_table,
        ],
    )

    refresh_context_button.click(
        fn=refresh_context_ui,
        inputs=[],
        outputs=CONTEXT_OUTPUTS,
    )

    save_general_button.click(
        fn=save_general_settings_ui,
        inputs=[
            open_browser_setting,
        ],
        outputs=[
            general_settings_status,
        ],
    )

    save_model_button.click(
        fn=save_model_settings_ui,
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
        ],
    )

    test_model_button.click(
        fn=test_model_connection_ui,
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
        fn=migrate_api_key_ui,
        inputs=[],
        outputs=[
            model_settings_status,
            api_key_status_box,
        ],
    )

    delete_api_key_button.click(
        fn=delete_api_key_ui,
        inputs=[],
        outputs=[
            model_settings_status,
            api_key_status_box,
        ],
    )

    save_agent_button.click(
        fn=save_agent_settings_ui,
        inputs=[
            max_turns_setting,
        ],
        outputs=[
            agent_settings_status,
        ],
    )

    save_web_button.click(
        fn=save_web_settings_ui,
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
        fn=mcp_refresh_ui,
        inputs=[],
        outputs=[
            mcp_server_selector,
            mcp_server_table,
            mcp_dependency_box,
        ],
    )

    mcp_new_button.click(
        fn=mcp_empty_form,
        inputs=[],
        outputs=[
            mcp_name,
            mcp_transport,
            mcp_enabled,
            mcp_approval,
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
        fn=mcp_save_ui,
        inputs=[
            mcp_server_id_state,
            mcp_name,
            mcp_transport,
            mcp_enabled,
            mcp_approval,
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
    )

    mcp_test_button.click(
        fn=mcp_test_ui,
        inputs=[
            mcp_server_id_state,
        ],
        outputs=[
            mcp_status_box,
            mcp_tools_table,
        ],
    )

    mcp_delete_button.click(
        fn=mcp_delete_ui,
        inputs=[
            mcp_server_id_state,
        ],
        outputs=[
            mcp_server_selector,
            mcp_server_table,
            mcp_server_id_state,
            mcp_status_box,
        ],
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

    refresh_conversations_button.click(
        fn=refresh_conversations_ui,
        inputs=[],
        outputs=[
            conversation_selector,
        ],
    )

    rename_conversation_button.click(
        fn=rename_conversation_ui,
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
            fn=delete_conversation_ui,
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


# ============================================================
# Composer Keyboard UX
# ============================================================

KEYBOARD_JS = r"""
() => {
    if (window.__xiaozhiComposerKeyboardInstalled) {
        return;
    }

    window.__xiaozhiComposerKeyboardInstalled = true;

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


# ============================================================
# Main
# 注意：只有 Main 退出 Blocks
# ============================================================

if __name__ == "__main__":

    demo.queue(
        default_concurrency_limit=1
    )

    demo.launch(
        inbrowser=get_bool_setting(
            "general.open_browser",
            True,
        ),
        show_error=True,
        theme=gr.themes.Base(),
        css=CSS,
        js=KEYBOARD_JS,
    )
