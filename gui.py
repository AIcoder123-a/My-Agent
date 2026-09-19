import asyncio
import json
import time

from pathlib import Path

import gradio as gr

from agent_service import (
    AgentService,
)

from tool_logging import (
    read_audit_logs,
)


# =========================================
# 路径
# =========================================

PROJECT_ROOT = (
    Path(__file__).resolve().parent
)

WORKSPACE_DIR = (
    PROJECT_ROOT / "workspace"
)

WORKSPACE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

MAX_PREVIEW_BYTES = 200_000


# =========================================
# Agent 服务
# =========================================

service = AgentService()


# =========================================
# 中文名称
# =========================================

TOOL_NAME_ZH = {
    "list_files": "列出文件",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "calculator": "计算器",
    "get_current_time": "获取当前时间",
    "save_note": "保存笔记",
    "read_notes": "读取笔记",
    "finish_task": "结束任务",
}


EVENT_NAME_ZH = {
    "task_started": "任务开始",
    "task_completed": "任务完成",
    "task_failed": "任务失败",

    "tool_start": "工具开始",
    "tool_success": "工具成功",
    "tool_error": "工具失败",

    "approval_requested": "请求审批",
    "approval_approved": "审批通过",
    "approval_rejected": "审批拒绝",
}


ERROR_TEXT_ZH = {
    "TOOL_ERROR": "工具错误",
    "FILE_NOT_FOUND": "文件不存在",
    "PERMISSION_DENIED": "权限不足",
    "IS_DIRECTORY": "路径是目录",
    "NOT_DIRECTORY": "路径不是目录",
    "INVALID_VALUE": "参数值无效",
    "DIVISION_BY_ZERO": "除数不能为零",
    "EXECUTION_FAILED": "执行失败",
    "FileNotFoundError": "文件不存在错误",
    "PermissionError": "权限错误",
    "ValueError": "参数值错误",
}


# =========================================
# 页面样式
# =========================================

CSS = """
.gradio-container {
    max-width: 1650px !important;
}

#main-title {
    margin-bottom: 2px;
}

#status-panel {
    min-height: 105px;
}

#approval-panel {
    min-height: 130px;
}

#workspace-preview textarea {
    font-family:
        "JetBrains Mono",
        "Consolas",
        "Microsoft YaHei",
        monospace;
}

footer {
    display: none !important;
}
"""


# =========================================
# 通用辅助
# =========================================

def tool_name_zh(
    tool_name,
) -> str:

    if not tool_name:
        return ""

    return TOOL_NAME_ZH.get(
        str(tool_name),
        "未知工具",
    )


def event_name_zh(
    event_name,
) -> str:

    if not event_name:
        return ""

    return EVENT_NAME_ZH.get(
        str(event_name),
        "其他事件",
    )


def translate_error_text(
    text,
) -> str:

    text = str(
        text or ""
    )

    for source, target in (
        ERROR_TEXT_ZH.items()
    ):

        text = text.replace(
            source,
            target,
        )

    return text


def compact_value(
    value,
    max_length=400,
) -> str:

    if value is None:
        return ""

    if isinstance(
        value,
        (dict, list),
    ):

        text = json.dumps(
            value,
            ensure_ascii=False,
        )

    else:

        text = str(value)

    if len(text) > max_length:

        return (
            text[:max_length]
            + "……"
        )

    return text


def disabled_button(
    label,
    variant="secondary",
):

    return gr.Button(
        label,
        interactive=False,
        variant=variant,
    )


def enabled_button(
    label,
    variant="secondary",
):

    return gr.Button(
        label,
        interactive=True,
        variant=variant,
    )


def idle_approval_text():

    return (
        "### 人工审批\n\n"
        "当前没有等待审批的操作。"
    )


# =========================================
# 审批显示
# =========================================

def format_arguments_for_ui(
    arguments,
) -> str:

    if isinstance(
        arguments,
        str,
    ):

        try:

            arguments = json.loads(
                arguments
            )

        except Exception:

            pass

    if isinstance(
        arguments,
        (dict, list),
    ):

        text = json.dumps(
            arguments,
            ensure_ascii=False,
            indent=2,
        )

    else:

        text = str(
            arguments
        )

    if len(text) > 6000:

        text = (
            text[:6000]
            + "\n\n"
            "……参数内容较长，"
            "这里只显示前面的部分。"
        )

    return text


def approval_markdown(
    result,
):

    tool = tool_name_zh(
        result.get(
            "tool"
        )
    )

    arguments = (
        format_arguments_for_ui(
            result.get(
                "arguments",
                {},
            )
        )
    )

    remaining = result.get(
        "remaining",
        1,
    )

    return (
        "### ⚠ 需要你的批准\n\n"
        f"**准备执行：** {tool}\n\n"
        "**操作参数：**\n"
        f"```text\n"
        f"{arguments}\n"
        f"```\n\n"
        f"当前还有 **{remaining}** "
        "个操作等待审批。"
    )


# =========================================
# 审计日志
# =========================================

def record_details(
    record,
) -> str:

    event = record.get(
        "event",
        "",
    )

    if event in {
        "tool_start",
        "approval_requested",
    }:

        value = record.get(
            "arguments",
            "",
        )

    elif event == "tool_success":

        value = record.get(
            "result",
            "",
        )

    elif event in {
        "tool_error",
        "task_failed",
    }:

        value = (
            f"{record.get('error_type', '')}: "
            f"{record.get('error', '')}"
        )

    else:

        value = ""

    return translate_error_text(
        compact_value(
            value
        )
    )


def recent_audit_rows():

    records = read_audit_logs(
        limit=100
    )

    rows = []

    for record in records:

        rows.append(
            [
                record.get(
                    "time",
                    "",
                ),
                record.get(
                    "session_id",
                    "",
                ),
                record.get(
                    "task_id",
                    "",
                ),
                event_name_zh(
                    record.get(
                        "event"
                    )
                ),
                tool_name_zh(
                    record.get(
                        "tool"
                    )
                ),
                record.get(
                    "duration_ms",
                    "",
                ),
                record_details(
                    record
                ),
            ]
        )

    return rows


def task_trace_from_audit(
    task_id,
):

    if not task_id:
        return []

    records = read_audit_logs(
        limit=500,
        task_id=task_id,
    )

    rows = []

    for record in records:

        event = record.get(
            "event",
            "",
        )

        if event == "tool_start":
            state = "执行中"

        elif event == "tool_success":
            state = "成功"

        elif event == "tool_error":
            state = "失败"

        elif event == "approval_requested":
            state = "等待审批"

        elif event == "approval_approved":
            state = "已批准"

        elif event == "approval_rejected":
            state = "已拒绝"

        elif event == "task_completed":
            state = "已完成"

        elif event == "task_failed":
            state = "失败"

        else:
            state = ""

        rows.append(
            [
                record.get(
                    "time",
                    "",
                ),
                event_name_zh(
                    event
                ),
                tool_name_zh(
                    record.get(
                        "tool"
                    )
                ),
                state,
                record.get(
                    "duration_ms",
                    "",
                ),
                record_details(
                    record
                ),
            ]
        )

    return rows


# =========================================
# 工作区
# =========================================

def safe_workspace_file(
    selected,
):

    if not selected:
        return None

    if isinstance(
        selected,
        list,
    ):

        if not selected:
            return None

        selected = selected[0]

    target = Path(
        selected
    )

    if not target.is_absolute():

        target = (
            WORKSPACE_DIR
            / target
        )

    target = target.resolve()

    root = (
        WORKSPACE_DIR.resolve()
    )

    try:

        target.relative_to(
            root
        )

    except ValueError:

        raise PermissionError(
            "禁止访问工作区以外的文件。"
        )

    return target


def preview_workspace_file(
    selected,
):

    try:

        target = safe_workspace_file(
            selected
        )

        if target is None:

            return (
                "请从左侧选择一个文件。"
            )

        if not target.exists():

            return (
                "文件不存在，"
                "请刷新文件列表。"
            )

        if target.is_dir():

            items = sorted(
                target.iterdir(),
                key=lambda p: (
                    p.name.lower()
                ),
            )

            if not items:

                return "该目录为空。"

            lines = []

            for item in items:

                if item.is_dir():

                    prefix = "[目录]"

                else:

                    prefix = "[文件]"

                lines.append(
                    f"{prefix} "
                    f"{item.name}"
                )

            return "\n".join(
                lines
            )

        file_size = (
            target.stat().st_size
        )

        with target.open(
            "rb"
        ) as file:

            data = file.read(
                MAX_PREVIEW_BYTES
            )

        if b"\x00" in data:

            return (
                "这是二进制文件，"
                "无法按文本方式预览。\n\n"
                f"文件大小："
                f"{file_size} 字节"
            )

        try:

            content = data.decode(
                "utf-8"
            )

        except UnicodeDecodeError:

            content = data.decode(
                "utf-8",
                errors="replace",
            )

        if (
            file_size
            > MAX_PREVIEW_BYTES
        ):

            content += (
                "\n\n"
                "--------------------\n"
                "文件较大，当前只显示"
                f"前 {MAX_PREVIEW_BYTES} 字节。"
            )

        return content

    except Exception as e:

        return (
            "无法预览文件："
            f"{translate_error_text(e)}"
        )


def refresh_workspace_tree():

    return gr.FileExplorer(
        root_dir=str(
            WORKSPACE_DIR
        ),
        glob="**/*",
        file_count="single",
        interactive=True,
        height=500,
        label="工作区文件",
    )


# =========================================
# 实时轨迹
# =========================================

def append_stream_trace(
    rows,
    event,
):

    rows = list(
        rows or []
    )

    code = event.get(
        "event"
    )

    time_text = event.get(
        "time",
        "",
    )

    tool = tool_name_zh(
        event.get(
            "tool"
        )
    )

    if code == "task_started":

        rows.append(
            [
                time_text,
                "任务开始",
                "",
                "运行中",
                "",
                "开始处理用户任务。",
            ]
        )

    elif code == "tool_started":

        rows.append(
            [
                time_text,
                "调用工具",
                tool,
                "执行中",
                "",
                compact_value(
                    event.get(
                        "arguments"
                    )
                ),
            ]
        )

    elif code == "tool_completed":

        rows.append(
            [
                time_text,
                "工具完成",
                tool,
                "成功",
                event.get(
                    "duration_ms",
                    "",
                ),
                compact_value(
                    event.get(
                        "output"
                    )
                ),
            ]
        )

    elif code == "tool_failed":

        rows.append(
            [
                time_text,
                "工具完成",
                tool,
                "失败",
                event.get(
                    "duration_ms",
                    "",
                ),
                translate_error_text(
                    compact_value(
                        event.get(
                            "output"
                        )
                    )
                ),
            ]
        )

    elif code == "approval_approved":

        rows.append(
            [
                time_text,
                "人工审批",
                tool,
                "已批准",
                "",
                "你允许了这个操作。",
            ]
        )

    elif code == "approval_rejected":

        rows.append(
            [
                time_text,
                "人工审批",
                tool,
                "已拒绝",
                "",
                "你拒绝了这个操作。",
            ]
        )

    return rows


# =========================================
# 对话内容处理
# =========================================

def set_assistant_text(
    history,
    text,
):

    history = list(
        history or []
    )

    if (
        history
        and history[-1].get(
            "role"
        )
        == "assistant"
    ):

        history[-1] = {
            "role": "assistant",
            "content": text,
        }

    else:

        history.append(
            {
                "role": "assistant",
                "content": text,
            }
        )

    return history


# =========================================
# 核心：把 Service Stream 渲染到 UI
# =========================================

async def render_service_stream(
    stream,
    history,
    trace_rows,
):

    history = list(
        history or []
    )

    trace_rows = list(
        trace_rows or []
    )

    task_id = ""

    assistant_buffer = ""

    if (
        history
        and history[-1].get(
            "role"
        )
        == "assistant"
    ):

        assistant_buffer = str(
            history[-1].get(
                "content",
                "",
            )
        )

    last_text_yield = 0.0

    async for event in stream:

        # =========================
        # 文本流
        # =========================

        if (
            event.get("event")
            == "text_delta"
        ):

            assistant_buffer += (
                event.get(
                    "delta",
                    "",
                )
            )

            history = (
                set_assistant_text(
                    history,
                    assistant_buffer,
                )
            )

            # 不必每个 token 都刷新页面，
            # 约 25 FPS 已经很流畅。
            now = time.monotonic()

            if (
                now - last_text_yield
                >= 0.04
            ):

                last_text_yield = now

                yield (
                    history,
                    (
                        "### 当前状态\n"
                        "⏳ **正在运行**"
                    ),
                    idle_approval_text(),
                    disabled_button(
                        "批准"
                    ),
                    disabled_button(
                        "拒绝",
                        "stop",
                    ),
                    disabled_button(
                        "发送",
                        "primary",
                    ),
                    disabled_button(
                        "新建会话"
                    ),
                    task_id,
                    trace_rows,
                    trace_rows,
                )

            continue

        # =========================
        # 实时任务事件
        # =========================

        if "event" in event:

            trace_rows = (
                append_stream_trace(
                    trace_rows,
                    event,
                )
            )

            if event.get(
                "task_id"
            ):

                task_id = str(
                    event.get(
                        "task_id"
                    )
                )

            yield (
                history,
                (
                    "### 当前状态\n"
                    "⏳ **正在运行**"
                ),
                idle_approval_text(),
                disabled_button(
                    "批准"
                ),
                disabled_button(
                    "拒绝",
                    "stop",
                ),
                disabled_button(
                    "发送",
                    "primary",
                ),
                disabled_button(
                    "新建会话"
                ),
                task_id,
                trace_rows,
                trace_rows,
            )

            continue

        # =========================
        # 等待人工审批
        # =========================

        if (
            event.get("status")
            == "approval_required"
        ):

            task_id = str(
                event.get(
                    "task_id",
                    "",
                )
            )

            trace_rows.append(
                [
                    event.get(
                        "time",
                        "",
                    ),
                    "请求审批",
                    tool_name_zh(
                        event.get(
                            "tool"
                        )
                    ),
                    "等待审批",
                    "",
                    "等待你的决定。",
                ]
            )

            yield (
                history,
                (
                    "### 当前状态\n"
                    "⏸ **等待你的批准**"
                ),
                approval_markdown(
                    event
                ),
                enabled_button(
                    "批准",
                    "primary",
                ),
                enabled_button(
                    "拒绝",
                    "stop",
                ),
                disabled_button(
                    "发送",
                    "primary",
                ),
                disabled_button(
                    "新建会话"
                ),
                task_id,
                trace_rows,
                trace_rows,
            )

            return

        # =========================
        # 完成
        # =========================

        if (
            event.get("status")
            == "completed"
        ):

            task_id = str(
                event.get(
                    "task_id",
                    "",
                )
            )

            final_text = str(
                event.get(
                    "message",
                    "任务已完成。",
                )
            )

            history = (
                set_assistant_text(
                    history,
                    final_text,
                )
            )

            trace_rows.append(
                [
                    event.get(
                        "time",
                        "",
                    ),
                    "任务完成",
                    "",
                    "已完成",
                    "",
                    "任务已经全部完成。",
                ]
            )

            yield (
                history,
                (
                    "### 当前状态\n"
                    "✅ **任务已完成**"
                ),
                idle_approval_text(),
                disabled_button(
                    "批准"
                ),
                disabled_button(
                    "拒绝",
                    "stop",
                ),
                enabled_button(
                    "发送",
                    "primary",
                ),
                enabled_button(
                    "新建会话"
                ),
                task_id,
                trace_rows,
                trace_rows,
            )

            return

        # =========================
        # 失败
        # =========================

        if (
            event.get("status")
            == "error"
        ):

            task_id = str(
                event.get(
                    "task_id",
                    "",
                )
            )

            message = (
                translate_error_text(
                    event.get(
                        "message",
                        "发生未知错误。",
                    )
                )
            )

            history = (
                set_assistant_text(
                    history,
                    (
                        "任务执行失败：\n\n"
                        f"{message}"
                    ),
                )
            )

            trace_rows.append(
                [
                    event.get(
                        "time",
                        "",
                    ),
                    "任务结束",
                    "",
                    "失败",
                    "",
                    message,
                ]
            )

            yield (
                history,
                (
                    "### 当前状态\n"
                    "❌ **任务执行失败**"
                ),
                idle_approval_text(),
                disabled_button(
                    "批准"
                ),
                disabled_button(
                    "拒绝",
                    "stop",
                ),
                enabled_button(
                    "发送",
                    "primary",
                ),
                enabled_button(
                    "新建会话"
                ),
                task_id,
                trace_rows,
                trace_rows,
            )

            return


# =========================================
# 发送任务
# =========================================

def stage_user_message(
    message,
    history,
):

    message = (
        message or ""
    ).strip()

    history = list(
        history or []
    )

    if not message:

        return (
            history,
            "",
            None,
            (
                "### 当前状态\n"
                "请输入任务内容。"
            ),
            idle_approval_text(),
            disabled_button(
                "批准"
            ),
            disabled_button(
                "拒绝",
                "stop",
            ),
            enabled_button(
                "发送",
                "primary",
            ),
            enabled_button(
                "新建会话"
            ),
            "",
            [],
            [],
        )

    history.append(
        {
            "role": "user",
            "content": message,
        }
    )

    return (
        history,
        "",
        message,
        (
            "### 当前状态\n"
            "⏳ **正在启动任务**"
        ),
        idle_approval_text(),
        disabled_button(
            "批准"
        ),
        disabled_button(
            "拒绝",
            "stop",
        ),
        disabled_button(
            "发送",
            "primary",
        ),
        disabled_button(
            "新建会话"
        ),
        "",
        [],
        [],
    )


async def run_message_stream(
    message,
    history,
    trace_rows,
):

    if not message:

        return

    async for output in (
        render_service_stream(
            service.stream_task(
                message
            ),
            history,
            trace_rows,
        )
    ):

        yield output


# =========================================
# 审批
# =========================================

async def approve_stream(
    history,
    trace_rows,
):

    async for output in (
        render_service_stream(
            service.stream_approval(
                approved=True
            ),
            history,
            trace_rows,
        )
    ):

        yield output


async def reject_stream(
    history,
    trace_rows,
):

    async for output in (
        render_service_stream(
            service.stream_approval(
                approved=False
            ),
            history,
            trace_rows,
        )
    ):

        yield output


# =========================================
# 会话
# =========================================

def create_new_session():

    session_id = (
        service.new_session()
    )

    return (
        [],
        session_id,
        (
            "### 当前状态\n"
            "🟢 **就绪**\n\n"
            "已经创建新的会话。"
        ),
        idle_approval_text(),
        disabled_button(
            "批准"
        ),
        disabled_button(
            "拒绝",
            "stop",
        ),
        enabled_button(
            "发送",
            "primary",
        ),
        "",
        [],
        [],
    )


def clear_chat():

    return []


# =========================================
# 刷新轨迹
# =========================================

def refresh_task_trace(
    task_id,
):

    rows = (
        task_trace_from_audit(
            task_id
        )
    )

    return (
        rows,
        rows,
    )


# =========================================
# 页面
# =========================================

with gr.Blocks(
    title="小智智能体",
    fill_width=True,
    theme=gr.themes.Soft(),
    css=CSS,
) as demo:

    pending_message = gr.State(
        value=None
    )

    trace_state = gr.State(
        value=[]
    )

    gr.Markdown(
        "# 小智智能体",
        elem_id="main-title",
    )

    gr.Markdown(
        "本地人工智能智能体控制台 · "
        "工具调用 · 会话记忆 · "
        "人工审批 · 错误恢复 · "
        "实时执行过程"
    )

    with gr.Row():

        # =================================
        # 左侧控制区
        # =================================

        with gr.Column(
            scale=1,
            min_width=280,
        ):

            session_box = (
                gr.Textbox(
                    label="当前会话编号",
                    value=(
                        service
                        .get_session_id()
                    ),
                    interactive=False,
                )
            )

            task_id_box = (
                gr.Textbox(
                    label="当前或最近任务编号",
                    interactive=False,
                )
            )

            status_box = gr.Markdown(
                (
                    "### 当前状态\n"
                    "🟢 **就绪**"
                ),
                elem_id=(
                    "status-panel"
                ),
            )

            new_session_button = (
                gr.Button(
                    "新建会话"
                )
            )

            clear_chat_button = (
                gr.Button(
                    "清空对话界面"
                )
            )

            gr.Markdown(
                """
### 工具权限

**自动允许执行**

- 列出文件
- 读取文件
- 计算器
- 获取当前时间
- 保存和读取笔记

**必须经过你的批准**

- 写入文件

### 当前安全机制

- 工作区路径限制
- 写文件人工审批
- 工具错误自动恢复
- 操作审计记录
- 会话持久化
"""
            )

        # =================================
        # 右侧主体
        # =================================

        with gr.Column(
            scale=4,
            min_width=650,
        ):

            with gr.Tabs():

                # =========================
                # 对话
                # =========================

                with gr.Tab(
                    "对话"
                ):

                    chatbot = (
                        gr.Chatbot(
                            label="对话记录",
                            height=480,
                        )
                    )

                    message_box = (
                        gr.Textbox(
                            label="任务内容",
                            placeholder=(
                                "在这里输入你希望"
                                "小智完成的任务……\n"
                                "支持多行输入。"
                            ),
                            lines=5,
                        )
                    )

                    send_button = (
                        gr.Button(
                            "发送",
                            variant="primary",
                        )
                    )

                    approval_box = (
                        gr.Markdown(
                            idle_approval_text(),
                            elem_id=(
                                "approval-panel"
                            ),
                        )
                    )

                    with gr.Row():

                        approve_button = (
                            gr.Button(
                                "批准",
                                interactive=False,
                                variant="primary",
                            )
                        )

                        reject_button = (
                            gr.Button(
                                "拒绝",
                                interactive=False,
                                variant="stop",
                            )
                        )

                    gr.Markdown(
                        "### 实时执行过程"
                    )

                    task_trace = (
                        gr.Dataframe(
                            headers=[
                                "时间",
                                "事件",
                                "工具",
                                "状态",
                                "耗时（毫秒）",
                                "详情",
                            ],
                            value=[],
                            datatype="str",
                            type="array",
                            interactive=False,
                            max_height=400,
                        )
                    )

                    refresh_trace_button = (
                        gr.Button(
                            "刷新当前任务记录"
                        )
                    )

                # =========================
                # 工作区
                # =========================

                with gr.Tab(
                    "工作区"
                ):

                    gr.Markdown(
                        "### 工作区文件浏览"
                    )

                    gr.Markdown(
                        "这里只允许查看 "
                        "`workspace` "
                        "目录中的文件。"
                    )

                    with gr.Row():

                        with gr.Column(
                            scale=1
                        ):

                            workspace_explorer = (
                                gr.FileExplorer(
                                    root_dir=str(
                                        WORKSPACE_DIR
                                    ),
                                    glob="**/*",
                                    file_count=(
                                        "single"
                                    ),
                                    interactive=True,
                                    height=500,
                                    label=(
                                        "工作区文件"
                                    ),
                                )
                            )

                            refresh_workspace_button = (
                                gr.Button(
                                    "刷新文件列表"
                                )
                            )

                        with gr.Column(
                            scale=2
                        ):

                            workspace_preview = (
                                gr.Textbox(
                                    label="文件内容预览",
                                    lines=27,
                                    interactive=False,
                                    elem_id=(
                                        "workspace-preview"
                                    ),
                                )
                            )

                # =========================
                # 审计日志
                # =========================

                with gr.Tab(
                    "审计日志"
                ):

                    gr.Markdown(
                        "### 最近的操作记录"
                    )

                    audit_table = (
                        gr.Dataframe(
                            headers=[
                                "时间",
                                "会话编号",
                                "任务编号",
                                "事件",
                                "工具",
                                "耗时（毫秒）",
                                "详情",
                            ],
                            value=(
                                recent_audit_rows()
                            ),
                            datatype="str",
                            type="array",
                            interactive=False,
                            max_height=620,
                        )
                    )

                    refresh_audit_button = (
                        gr.Button(
                            "刷新审计日志"
                        )
                    )


    # =====================================
    # 用户发送消息
    # =====================================

    stage_event = (
        send_button.click(
            fn=stage_user_message,
            inputs=[
                message_box,
                chatbot,
            ],
            outputs=[
                chatbot,
                message_box,
                pending_message,
                status_box,
                approval_box,
                approve_button,
                reject_button,
                send_button,
                new_session_button,
                task_id_box,
                task_trace,
                trace_state,
            ],
            queue=False,
        )
    )

    run_event = stage_event.then(
        fn=run_message_stream,
        inputs=[
            pending_message,
            chatbot,
            trace_state,
        ],
        outputs=[
            chatbot,
            status_box,
            approval_box,
            approve_button,
            reject_button,
            send_button,
            new_session_button,
            task_id_box,
            task_trace,
            trace_state,
        ],
    )

    run_event.then(
        fn=recent_audit_rows,
        inputs=[],
        outputs=[
            audit_table
        ],
    )

    run_event.then(
        fn=refresh_workspace_tree,
        inputs=[],
        outputs=[
            workspace_explorer
        ],
    )


    # =====================================
    # 批准
    # =====================================

    approve_event = (
        approve_button.click(
            fn=approve_stream,
            inputs=[
                chatbot,
                trace_state,
            ],
            outputs=[
                chatbot,
                status_box,
                approval_box,
                approve_button,
                reject_button,
                send_button,
                new_session_button,
                task_id_box,
                task_trace,
                trace_state,
            ],
        )
    )

    approve_event.then(
        fn=recent_audit_rows,
        inputs=[],
        outputs=[
            audit_table
        ],
    )

    approve_event.then(
        fn=refresh_workspace_tree,
        inputs=[],
        outputs=[
            workspace_explorer
        ],
    )


    # =====================================
    # 拒绝
    # =====================================

    reject_event = (
        reject_button.click(
            fn=reject_stream,
            inputs=[
                chatbot,
                trace_state,
            ],
            outputs=[
                chatbot,
                status_box,
                approval_box,
                approve_button,
                reject_button,
                send_button,
                new_session_button,
                task_id_box,
                task_trace,
                trace_state,
            ],
        )
    )

    reject_event.then(
        fn=recent_audit_rows,
        inputs=[],
        outputs=[
            audit_table
        ],
    )


    # =====================================
    # 新建会话
    # =====================================

    new_session_button.click(
        fn=create_new_session,
        inputs=[],
        outputs=[
            chatbot,
            session_box,
            status_box,
            approval_box,
            approve_button,
            reject_button,
            send_button,
            task_id_box,
            task_trace,
            trace_state,
        ],
    )


    # =====================================
    # 清空聊天界面
    # =====================================

    clear_chat_button.click(
        fn=clear_chat,
        inputs=[],
        outputs=[
            chatbot
        ],
        queue=False,
    )


    # =====================================
    # 工作区
    # =====================================

    workspace_explorer.input(
        fn=preview_workspace_file,
        inputs=[
            workspace_explorer
        ],
        outputs=[
            workspace_preview
        ],
    )

    refresh_workspace_button.click(
        fn=refresh_workspace_tree,
        inputs=[],
        outputs=[
            workspace_explorer
        ],
    )


    # =====================================
    # 任务轨迹
    # =====================================

    refresh_trace_button.click(
        fn=refresh_task_trace,
        inputs=[
            task_id_box
        ],
        outputs=[
            task_trace,
            trace_state,
        ],
    )


    # =====================================
    # 审计日志
    # =====================================

    refresh_audit_button.click(
        fn=recent_audit_rows,
        inputs=[],
        outputs=[
            audit_table
        ],
    )


# =========================================
# 启动
# =========================================

if __name__ == "__main__":

    demo.queue(
        default_concurrency_limit=1
    )

    demo.launch(
        inbrowser=True,
        share=False,
    )