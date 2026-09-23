"""小智 Agent —— Gradio 界面组装与启动。

本文件只做两件事：
  1. 用 gr.Blocks 搭界面、绑定事件；
  2. 启动服务。

事件处理函数在 gui_handlers.py，样式与脚本在 gui_assets.py，
主题在 gui_theme.py，共享单例在 app_runtime.py。
原先这些全挤在一个 9900 行的文件里。
"""

import gradio as gr

from appearance import APPEARANCE_HTML, APPEARANCE_CSS
from plugins import PLUGIN_TABLE_HEADERS
from app_settings import get_bool_setting

from app_runtime import (
    AUDIT_HEADERS,
    TRACE_HEADERS,
    service,
)

from gui_assets import COMPOSER_MENU_HTML, CSS, KEYBOARD_JS
from gui_handlers import *  # noqa: F401,F403  事件处理函数

# 下划线开头不会被 import * 带过来，但布局初始化时要用到。
from gui_handlers import _settings_snapshot  # noqa: F401

from gui_theme import theme

# ============================================================
# 界面
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

                        undo_change_button = (
                            gr.Button(
                                "撤销上次改动"
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

    undo_change_button.click(
        fn=undo_last_change_ui,
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
