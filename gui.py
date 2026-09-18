import json

import gradio as gr

from agent_service import AgentService


# =========================================
# Agent Service
# =========================================

# GUI v1 定位：
# 本机、单用户、单浏览器实例。
service = AgentService()


# =========================================
# 辅助函数
# =========================================

def format_arguments(
    arguments,
) -> str:

    if isinstance(
        arguments,
        dict,
    ):

        return json.dumps(
            arguments,
            ensure_ascii=False,
            indent=2,
        )

    return str(arguments)


def approval_markdown(
    result: dict,
) -> str:

    tool = result.get(
        "tool",
        "未知工具",
    )

    arguments = format_arguments(
        result.get(
            "arguments",
            {},
        )
    )

    remaining = result.get(
        "remaining",
        1,
    )

    return (
        "### 需要人工批准\n\n"
        f"**工具：** `{tool}`\n\n"
        "**参数：**\n"
        f"```json\n{arguments}\n```\n"
        f"待处理审批数量：{remaining}"
    )


def idle_approval_text() -> str:

    return (
        "### 工具审批\n\n"
        "当前没有等待审批的操作。"
    )


# =========================================
# 将 Service Result 渲染到 GUI
# =========================================

def render_result(
    result: dict,
    history,
):

    history = list(
        history or []
    )

    status = result.get(
        "status"
    )

    # -------------------------
    # 完成
    # -------------------------

    if status == "completed":

        history.append(
            {
                "role": "assistant",
                "content": result.get(
                    "message",
                    "任务已完成。",
                ),
            }
        )

        task_id = result.get(
            "task_id",
            "",
        )

        return (
            history,
            (
                f"**状态：** 已完成  \n"
                f"**Task ID：** `{task_id}`"
            ),
            idle_approval_text(),
            gr.Button(
                "批准",
                interactive=False,
            ),
            gr.Button(
                "拒绝",
                interactive=False,
            ),
        )

    # -------------------------
    # 等待审批
    # -------------------------

    if status == "approval_required":

        task_id = result.get(
            "task_id",
            "",
        )

        return (
            history,
            (
                "**状态：** 等待人工审批  \n"
                f"**Task ID：** `{task_id}`"
            ),
            approval_markdown(
                result
            ),
            gr.Button(
                "批准",
                interactive=True,
                variant="primary",
            ),
            gr.Button(
                "拒绝",
                interactive=True,
                variant="stop",
            ),
        )

    # -------------------------
    # 错误
    # -------------------------

    error_message = result.get(
        "message",
        "发生未知错误。",
    )

    history.append(
        {
            "role": "assistant",
            "content": (
                f"**运行错误：** "
                f"{error_message}"
            ),
        }
    )

    return (
        history,
        "**状态：** 运行错误",
        idle_approval_text(),
        gr.Button(
            "批准",
            interactive=False,
        ),
        gr.Button(
            "拒绝",
            interactive=False,
        ),
    )


# =========================================
# GUI Events
# =========================================

def send_message(
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
            "**状态：** 请输入任务。",
            idle_approval_text(),
            gr.Button(
                "批准",
                interactive=False,
            ),
            gr.Button(
                "拒绝",
                interactive=False,
            ),
        )

    # 先显示用户消息
    history.append(
        {
            "role": "user",
            "content": message,
        }
    )

    result = service.start_task(
        message
    )

    (
        history,
        status,
        approval,
        approve_button,
        reject_button,
    ) = render_result(
        result,
        history,
    )

    return (
        history,
        "",
        status,
        approval,
        approve_button,
        reject_button,
    )


def approve_action(
    history,
):

    result = (
        service.approve_current()
    )

    return render_result(
        result,
        history,
    )


def reject_action(
    history,
):

    result = (
        service.reject_current()
    )

    return render_result(
        result,
        history,
    )


def new_session():

    session_id = (
        service.new_session()
    )

    return (
        [],
        session_id,
        (
            "**状态：** 已创建新的 Session。"
        ),
        idle_approval_text(),
        gr.Button(
            "批准",
            interactive=False,
        ),
        gr.Button(
            "拒绝",
            interactive=False,
        ),
    )


# =========================================
# GUI
# =========================================

with gr.Blocks(
    title="小智 Agent",
    fill_width=True,
) as demo:

    gr.Markdown(
        "# 小智 Agent\n"
        "本地 AI Agent 控制台"
    )

    with gr.Row():

        # =========================
        # 左侧
        # =========================

        with gr.Column(
            scale=1,
            min_width=260,
        ):

            session_box = gr.Textbox(
                label="当前 Session",
                value=(
                    service.get_session_id()
                ),
                interactive=False,
            )

            new_session_button = (
                gr.Button(
                    "新建 Session"
                )
            )

            status_box = gr.Markdown(
                "**状态：** 就绪"
            )

            gr.Markdown(
                "### 当前能力\n"
                "- SQLite Session\n"
                "- 多步骤 Agent\n"
                "- Workspace Tools\n"
                "- Human-in-the-loop\n"
                "- Tool Error Recovery\n"
                "- Audit Log"
            )

        # =========================
        # 右侧
        # =========================

        with gr.Column(
            scale=3,
        ):

            chatbot = gr.Chatbot(
                label="对话",
                height=520,
            )

            message_box = gr.Textbox(
                label="任务",
                placeholder=(
                    "输入你的任务……\n"
                    "支持多行输入。"
                ),
                lines=5,
            )

            send_button = gr.Button(
                "发送",
                variant="primary",
            )

            gr.Markdown("---")

            approval_box = gr.Markdown(
                idle_approval_text()
            )

            with gr.Row():

                approve_button = gr.Button(
                    "批准",
                    interactive=False,
                    variant="primary",
                )

                reject_button = gr.Button(
                    "拒绝",
                    interactive=False,
                    variant="stop",
                )

    # =====================================
    # Events
    # =====================================

    send_button.click(
        fn=send_message,
        inputs=[
            message_box,
            chatbot,
        ],
        outputs=[
            chatbot,
            message_box,
            status_box,
            approval_box,
            approve_button,
            reject_button,
        ],
    )

    approve_button.click(
        fn=approve_action,
        inputs=[
            chatbot,
        ],
        outputs=[
            chatbot,
            status_box,
            approval_box,
            approve_button,
            reject_button,
        ],
    )

    reject_button.click(
        fn=reject_action,
        inputs=[
            chatbot,
        ],
        outputs=[
            chatbot,
            status_box,
            approval_box,
            approve_button,
            reject_button,
        ],
    )

    new_session_button.click(
        fn=new_session,
        inputs=[],
        outputs=[
            chatbot,
            session_box,
            status_box,
            approval_box,
            approve_button,
            reject_button,
        ],
    )


if __name__ == "__main__":

    demo.queue()

    demo.launch(
        inbrowser=True,
        share=False,
    )