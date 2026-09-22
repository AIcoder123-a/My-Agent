from context_manager import (
    build_session_input_callback,
    get_context_status,
    get_compaction_plan,
    maybe_compact_context,
)
import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from threading import RLock
from time import perf_counter

from agents import (
    Runner,
    RunConfig,
)

from app_agents.personal_agent import (
    personal_agent,
)

from memory import (
    load_or_create_session,
    create_new_session,
    list_sessions as memory_list_sessions,
    switch_session as memory_switch_session,
    rename_session as memory_rename_session,
    delete_session as memory_delete_session,
    get_chat_history as memory_get_chat_history,
    touch_session,
)

from tool_logging import (
    start_task_context,
    set_task_context,
    write_log,
    sanitize_tool_arguments,
)

from agent_tools.web_search import (
    begin_search_task,
    activate_search_task,
    end_search_task,
    get_search_task_sources,
)

from app_settings import (
    get_int_setting,
)

from mcp_manager import (
    build_mcp_server,
    list_mcp_servers,
)


# ============================================================
# Runner 配置
# ============================================================

def build_run_config(
    session_id: str,
) -> RunConfig:

    return RunConfig(
        tool_not_found_behavior=(
            "return_error_to_model"
        ),

        session_input_callback=(
            build_session_input_callback(
                session_id
            )
        ),
    )


def now_text() -> str:
    """
    返回用于界面显示的当前时间。
    """

    return datetime.now().strftime(
        "%H:%M:%S"
    )


def _mcp_display_name(
    server,
) -> str:
    """
    MCP Server 的中文显示名。

    SDK 内部使用 ASCII name 生成工具名，
    但界面 / 审计日志应显示中文名。
    """

    display = str(
        getattr(
            server,
            "display_name",
            "",
        )
        or ""
    ).strip()

    if display:

        return display

    return str(
        getattr(
            server,
            "name",
            "MCP",
        )
        or "MCP"
    )


def raw_field(
    item,
    field_name: str,
):
    """
    安全读取 SDK RunItem 的原始字段。
    """

    raw_item = getattr(
        item,
        "raw_item",
        None,
    )

    if isinstance(
        raw_item,
        Mapping,
    ):
        return raw_item.get(
            field_name
        )

    return getattr(
        raw_item,
        field_name,
        None,
    )


def classify_tool_output(
    tool_name: str,
    output,
) -> tuple[bool, str]:
    """
    判断 Tool Output 在“业务层”是否应视为失败。

    SDK 层只要 Python Tool 正常 return，
    就会产生正常的 tool_output 事件。

    但某些 Tool 会使用结构化返回值表达业务失败，例如：

        {
            "ok": false,
            "error": "WEB_SEARCH_ERROR: ..."
        }

    这种情况虽然 Tool 函数本身没有抛异常，
    GUI / Streaming 仍应显示为 tool_failed。

    Returns:
        (is_error, display_output)
    """

    output_text = str(
        output
        if output is not None
        else ""
    )

    stripped = output_text.strip()

    # --------------------------------------------------------
    # 通用旧错误格式
    # --------------------------------------------------------

    if stripped.startswith(
        "TOOL_ERROR:"
    ):
        return True, output_text

    if tool_name == "run_shell":
        try:
            payload = json.loads(stripped)
            return (
                payload.get("status") != "completed" or payload.get("exit_code") != 0,
                output_text,
            )
        except (ValueError, AttributeError):
            return True, output_text

    # --------------------------------------------------------
    # Web Search 结构化业务结果
    # --------------------------------------------------------

    if tool_name in {
        "web_search",
        "web_fetch",
        "github_trending",
    }:

        try:
            payload = json.loads(
                stripped
            )
        except (
            json.JSONDecodeError,
            TypeError,
        ):
            payload = None

        if isinstance(
            payload,
            dict,
        ):

            if payload.get("ok") is False:

                if tool_name == "web_search":
                    default_error = (
                        "WEB_SEARCH_ERROR: 联网搜索失败。"
                    )
                elif tool_name == "web_fetch":
                    default_error = (
                        "WEB_FETCH_ERROR: 网页读取失败。"
                    )
                else:
                    default_error = (
                        "GITHUB_TRENDING_ERROR: "
                        "GitHub 热门数据读取失败。"
                    )

                error_text = str(
                    payload.get("error")
                    or default_error
                ).strip()

                return (
                    True,
                    error_text,
                )

    return False, output_text


class AgentService:
    """
    Agent 统一运行服务。

    CLI 和 GUI 共用这一层。

    支持：
    - SQLite Session
    - 普通同步运行
    - 实时流式运行
    - 人工审批
    - 审批后恢复
    - Tool 实时事件
    - Audit Log
    """

    def __init__(self):

        self.session, self.session_id = (
            load_or_create_session()
        )

        self.task_id = None

        self.pending_state = None

        self.pending_interruptions = []

        # 保存当前 Task 中正在执行/等待审批的 Tool Call。
        #
        # 必须放在实例层，而不是 _run_streamed() 的局部变量中，
        # 因为 HITL 审批会让一次任务跨越两次 run_streamed()。
        self.active_calls = {}

        self.running = False

        # 用户主动请求停止当前 Task。
        #
        # 由 GUI “停止”按钮设置，
        # stream_task 的事件循环在下一个
        # 事件点安全退出。
        self._cancel_requested = False

        # 当前 Task 的 MCP Server 对象。
        #
        # Server 配置只在新 Task 开始时快照一次。
        # HITL 暂停时会断开连接，但保留这些对象；
        # 审批恢复时再重新 connect。
        self.mcp_servers = []

        # 避免 MCP 工具和内置 FunctionTool 重名。
        # 例如 Filesystem MCP 也有 read_file / write_file。
        current_mcp_config = getattr(
            personal_agent,
            "mcp_config",
            {},
        )

        if not isinstance(
            current_mcp_config,
            dict,
        ):
            current_mcp_config = {}

        personal_agent.mcp_config = {
            **current_mcp_config,
            "include_server_in_tool_names":
                True,
        }

        personal_agent.mcp_servers = []

        self.lock = RLock()

        # ------------------------------------------------
        # MCP 连接池
        #
        # npx / uvx 这类 stdio Server 冷启动实测需要 20 秒以上，
        # 如果每个 Task 都重新 connect，
        # 用户每发一句话都要先干等 20 秒——表现就是"点了没反应"。
        #
        # 因此 Task 结束后保留连接，
        # 只有在配置变化或连接出错时才重建。
        # ------------------------------------------------

        self._mcp_pool = []

        self._mcp_pool_signature = (
            None
        )

    # ========================================================
    # 基础状态
    # ========================================================

    def get_session_id(self) -> str:

        return self.session_id

    def get_task_id(
        self,
    ) -> str | None:

        return self.task_id
    def get_context_status(
        self,
    ) -> dict:
        """
        返回当前 Conversation 的
        Context 状态。
        """

        return get_context_status(
            self.session_id
        )

    def new_session(self) -> str:
        """
        创建全新的 Conversation Session。
        """

        with self.lock:

            if self.running or self.pending_interruptions:
                raise RuntimeError("请先停止当前任务或处理审批，再新建会话。")

            self.session, self.session_id = (
                create_new_session()
            )

            self.task_id = None

            self.pending_state = None

            self.pending_interruptions = []

            self.active_calls = {}

            self.running = False

            self._cancel_requested = (
                False
            )

            # 新会话不应继承上一个会话的
            # MCP 连接与 Task 状态。
            self._reset_mcp_task_state()

            return self.session_id
    # =====================================
    # Conversation / Context 管理
    # =====================================

    def list_conversations(self):
        """
        返回历史会话列表。
        """

        return memory_list_sessions()


    async def get_current_chat_history(self):
        """
        获取当前 Session 的聊天历史，
        转换为 GUI Chatbot 可直接使用的格式。
        """

        return await memory_get_chat_history(
            self.session_id
        )


    async def switch_conversation(
        self,
        session_id: str,
    ) -> dict:
        """
        切换到一个历史 Session。
        """

        with self.lock:

            if self.running:

                raise RuntimeError(
                    "当前任务正在运行，"
                    "不能切换会话。"
                )

            if self.pending_state is not None:

                raise RuntimeError(
                    "当前任务正在等待人工审批，"
                    "请先完成审批再切换会话。"
                )

            old_session = self.session

            new_session, new_session_id = (
                memory_switch_session(
                    session_id
                )
            )

            self.session = new_session
            self.session_id = new_session_id

            self.task_id = None
            self.pending_state = None
            self.pending_interruptions = []
            self.active_calls = {}
            self.running = False

        # 旧 SQLiteSession 不再使用，关闭连接。
        try:
            old_session.close()
        except Exception:
            pass

        history = await memory_get_chat_history(
            new_session_id
        )

        return {
            "session_id": new_session_id,
            "history": history,
        }


    def rename_current_conversation(
        self,
        title: str,
    ) -> str:
        """
        修改当前会话名称。
        """

        if self.running:

            raise RuntimeError(
                "当前任务正在运行，"
                "暂时不能重命名会话。"
            )

        new_title = memory_rename_session(
            self.session_id,
            title,
        )

        return new_title

    async def delete_conversation(
            self,
            session_id: str,
    ) -> dict:
        """
        删除指定 Conversation。

        如果删除当前会话：
        1. 优先切换到最近的其他历史会话
        2. 如果已经没有其他会话，才创建新会话
        """

        session_id = (
                session_id or ""
        ).strip()

        if not session_id:
            raise ValueError(
                "Session ID 不能为空。"
            )

        # =====================================
        # 状态检查
        # =====================================

        with self.lock:

            if self.running:
                raise RuntimeError(
                    "当前任务正在运行，"
                    "不能删除会话。"
                )

            if self.pending_state is not None:
                raise RuntimeError(
                    "当前任务正在等待人工审批，"
                    "请先完成审批再删除会话。"
                )

            deleting_current = (
                    session_id
                    == self.session_id
            )

            old_session = (
                self.session
                if deleting_current
                else None
            )

        # =====================================
        # 删除当前 Session 前先释放连接
        # =====================================

        if (
                deleting_current
                and old_session is not None
        ):

            try:
                old_session.close()
            except Exception:
                pass

        # =====================================
        # 真正删除
        # =====================================

        await memory_delete_session(
            session_id
        )

        # =====================================
        # 如果删除的是当前会话
        # =====================================

        if deleting_current:

            remaining_sessions = (
                memory_list_sessions()
            )

            # ---------------------------------
            # 还有历史 Session：
            # 自动切换到最近使用的那个
            # ---------------------------------

            if remaining_sessions:

                target_session_id = (
                    remaining_sessions[0][
                        "session_id"
                    ]
                )

                (
                    new_session,
                    new_session_id,
                ) = memory_switch_session(
                    target_session_id
                )

            # ---------------------------------
            # 一个 Session 都没有：
            # 才创建全新的空 Session
            # ---------------------------------

            else:

                (
                    new_session,
                    new_session_id,
                ) = create_new_session()

            with self.lock:

                self.session = new_session
                self.session_id = (
                    new_session_id
                )

                self.task_id = None
                self.pending_state = None
                self.pending_interruptions = []
                self.active_calls = {}
                self.running = False

        # =====================================
        # 删除的不是当前会话
        # =====================================

        else:

            new_session_id = (
                self.session_id
            )

        # =====================================
        # 重新读取当前会话历史
        # =====================================

        history = (
            await memory_get_chat_history(
                new_session_id
            )
        )

        return {
            "session_id":
                new_session_id,

            "history":
                history,

            "sessions":
                memory_list_sessions(),
        }
    # ========================================================
    # CLI 兼容层
    # ========================================================

    def start_task(
        self,
        user_input: str,
    ) -> dict:
        """
        CLI 使用的同步接口。

        内部实际上使用新的流式运行逻辑，
        这里只收集最终状态。
        """

        return asyncio.run(
            self._collect_terminal_result(
                self.stream_task(
                    user_input
                )
            )
        )

    def approve_current(
        self,
    ) -> dict:

        return asyncio.run(
            self._collect_terminal_result(
                self.stream_approval(
                    approved=True
                )
            )
        )

    def reject_current(
        self,
    ) -> dict:

        return asyncio.run(
            self._collect_terminal_result(
                self.stream_approval(
                    approved=False
                )
            )
        )

    async def _collect_terminal_result(
        self,
        stream,
    ) -> dict:

        terminal_result = {
            "status": "error",
            "message": (
                "任务没有产生最终状态。"
            ),
        }

        async for event in stream:

            if "status" in event:

                terminal_result = event

        return terminal_result

    # ========================================================
    # 新任务：流式运行
    # ========================================================

    # ========================================================
    # MCP Runtime
    # ========================================================

    def _build_enabled_mcp_servers(
        self,
    ) -> list:
        """
        读取当前设置中“已启用”的 MCP Server，
        为一个新 Task 构造独立的 Server 对象列表。

        配置在 Task 开始时快照；
        Task 中途修改设置不会影响正在运行的任务。
        """

        result = []

        for config in list_mcp_servers():

            if not config.get(
                "enabled",
                False,
            ):
                continue

            result.append(
                build_mcp_server(
                    config
                )
            )

        return result


    async def warmup_mcp_pool(
        self,
    ) -> None:
        """
        后台预热 MCP 连接池。

        页面打开时就悄悄连好，
        用户发第一句话时不必再等 npx 冷启动。
        """

        with self.lock:

            if self._mcp_pool:
                return

            if self.running:
                return

            servers = (
                self._build_enabled_mcp_servers()
            )

            if not servers:
                return

            self.mcp_servers = servers

        try:

            await self._connect_mcp_servers()

        except Exception:

            # 预热失败不影响正常使用：
            # 真正发任务时会按正常流程重试并给出失败提示。
            self._mcp_pool = []

            self._mcp_pool_signature = (
                None
            )

    def _mcp_config_signature(
        self,
    ) -> str:
        """
        当前「已启用」MCP 配置的指纹。

        配置没变就复用已有连接；
        配置一变（增删改、启停、改超时）指纹就变，
        下次任务会重新连接。
        """

        import hashlib
        import json

        payload = [
            config
            for config in list_mcp_servers()
            if config.get(
                "enabled",
                False,
            )
        ]

        try:

            raw = json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )

        except Exception:

            raw = repr(
                payload
            )

        return (
            hashlib.sha1(
                raw.encode(
                    "utf-8"
                )
            ).hexdigest()
        )

    async def reset_mcp_pool(
        self,
    ) -> None:
        """
        显式销毁连接池（MCP 配置变更时调用）。
        """

        servers = list(
            self._mcp_pool
        )

        self._mcp_pool = []

        self._mcp_pool_signature = (
            None
        )

        self.mcp_servers = []

        personal_agent.mcp_servers = (
            []
        )

        for server in servers:

            try:

                await server.cleanup()

            except Exception:
                pass

    async def _connect_mcp_servers(
        self,
        *,
        reconnect: bool = False,
    ):
        """
        连接当前 Task 的 MCP servers。

        初次连接：
        - 单个 Server 失败时丢弃该 Server；
        - 其它可用 Server 仍继续参与 Task。

        HITL 恢复重连：
        - 如果之前已经可用的 Server 无法重新连接，
          直接抛错，避免批准后的 MCP 操作在半失效状态下继续。
        """

        signature = (
            self._mcp_config_signature()
        )

        if not self.mcp_servers:

            personal_agent.mcp_servers = []

            self._mcp_pool = []

            self._mcp_pool_signature = (
                signature
            )

            return []

        # ------------------------------------------------
        # 复用已有连接
        # ------------------------------------------------

        if (
            self._mcp_pool
            and self._mcp_pool_signature
            == signature
            and all(
                server
                in self._mcp_pool
                for server in (
                    self.mcp_servers
                )
            )
        ):

            personal_agent.mcp_servers = (
                list(
                    self.mcp_servers
                )
            )

            write_log(
                {
                    "event":
                        "mcp_reused",
                    "count": len(
                        self.mcp_servers
                    ),
                }
            )

            return []

        connected = []
        events = []
        failures = []

        for server in list(
            self.mcp_servers
        ):

            server_name = (
                _mcp_display_name(
                    server
                )
            )

            # npx / uvx 这类 stdio Server 在包缓存冷启动时
            # 首次拉起经常超过默认超时，从而被误判为不可用。
            # 初次连接允许重试一次；HITL 恢复时不做额外重试，
            # 因为此时 RunState 已经在等待具体 Tool 的结果。
            attempts = 1 if reconnect else 2

            error = None
            connected_ok = False

            for attempt in range(attempts):

                try:

                    await server.connect()

                    connected_ok = True

                    break

                except Exception as attempt_error:

                    error = attempt_error

                    try:
                        await server.cleanup()
                    except Exception:
                        pass

                    if attempt + 1 < attempts:

                        await asyncio.sleep(1.5)

            if connected_ok:

                connected.append(
                    server
                )

                write_log(
                    {
                        "event": (
                            "mcp_reconnected"
                            if reconnect
                            else "mcp_connected"
                        ),
                        "server":
                            server_name,
                    }
                )

                events.append(
                    {
                        "event": (
                            "mcp_reconnected"
                            if reconnect
                            else "mcp_connected"
                        ),
                        "task_id":
                            self.task_id,
                        "server":
                            server_name,
                        "time":
                            now_text(),
                    }
                )

            else:

                failures.append(
                    (
                        server_name,
                        error,
                    )
                )

                try:
                    await server.cleanup()
                except Exception:
                    pass

                write_log(
                    {
                        "event":
                            "mcp_connection_failed",
                        "server":
                            server_name,
                        "error_type":
                            type(
                                error
                            ).__name__,
                        "error":
                            str(
                                error
                            )[:1000],
                    }
                )

                events.append(
                    {
                        "event":
                            "mcp_connection_failed",
                        "task_id":
                            self.task_id,
                        "server":
                            server_name,
                        "error": (
                            f"{type(error).__name__}: "
                            f"{error}"
                        ),
                        "time":
                            now_text(),
                    }
                )

        if reconnect and failures:

            # 恢复审批时，RunState 可能正等待调用某个 MCP Tool。
            # 此时不能静默丢弃已失效的 Server。
            personal_agent.mcp_servers = []

            names = ", ".join(
                name
                for name, _
                in failures
            )

            raise RuntimeError(
                "MCP 审批恢复时重新连接失败："
                f"{names}"
            )

        # 初始连接失败的 Server 从当前 Task 中移除。
        self.mcp_servers = connected

        personal_agent.mcp_servers = list(
            connected
        )

        # 记录连接池，供后续 Task 复用。
        self._mcp_pool = list(
            connected
        )

        self._mcp_pool_signature = (
            signature
        )

        return events


    async def _disconnect_mcp_servers(
        self,
        *,
        keep_for_resume: bool,
    ):
        """
        断开当前阶段的 MCP 连接。

        keep_for_resume=True:
            HITL 暂停。保留 Server 对象，
            后续 approval callback 重新 connect。

        keep_for_resume=False:
            Task 真正结束。清空 Server 对象与 Agent 挂载。
        """

        errors = []

        for server in reversed(
            list(
                self.mcp_servers
            )
        ):

            server_name = (
                _mcp_display_name(
                    server
                )
            )

            try:

                await server.cleanup()

                write_log(
                    {
                        "event":
                            "mcp_disconnected",
                        "server":
                            server_name,
                    }
                )

            except Exception as error:

                errors.append(
                    {
                        "server":
                            server_name,
                        "error": (
                            f"{type(error).__name__}: "
                            f"{error}"
                        ),
                    }
                )

                try:

                    write_log(
                        {
                            "event":
                                "mcp_cleanup_failed",
                            "server":
                                server_name,
                            "error_type":
                                type(
                                    error
                                ).__name__,
                            "error":
                                str(
                                    error
                                )[:1000],
                        }
                    )

                except Exception:
                    pass

        # 断开后不要让 Agent 在未连接状态下继续暴露 MCP Tools。
        personal_agent.mcp_servers = []

        if not keep_for_resume:

            self.mcp_servers = []

        return errors


    def _reset_mcp_task_state(
        self,
    ) -> None:

        self.mcp_servers = []

        personal_agent.mcp_servers = []


    async def stream_task(
        self,
        user_input: str,
    ):
        """
        流式执行一个新的用户任务。
        """

        user_input = (
            user_input or ""
        ).strip()

        if not user_input:

            yield {
                "status": "error",
                "message": (
                    "用户输入不能为空。"
                ),
                "time": now_text(),
            }

            return

        with self.lock:

            if self.running:

                yield {
                    "status": "error",
                    "message": (
                        "当前已有任务正在运行。"
                    ),
                    "time": now_text(),
                }

                return

            if (
                self.pending_state
                is not None
            ):

                yield {
                    "status": "error",
                    "message": (
                        "当前任务正在等待人工审批，"
                        "请先批准或拒绝当前操作。"
                    ),
                    "time": now_text(),
                }

                return

            self.running = True

            self._cancel_requested = (
                False
            )

            # 新 Task 开始时清空旧 Tool Call 信息。
            self.active_calls = {}

            self.task_id = (
                start_task_context(
                    self.session_id
                )
            )
            task_id = self.task_id

            touch_session(
                self.session_id
            )

            task_id = self.task_id

            # 为当前 Task 创建独立的联网搜索预算。
            # 普通任务最多 4 次；明显深度研究任务最多 6 次。
            begin_search_task(
                task_id,
                user_input,
            )

            # 为当前 Task 快照“已启用”的 MCP 配置。
            # 配置没变就复用上一次已经连好的对象，
            # 避免每句话都为 npx 冷启动付 20 秒。
            signature = (
                self._mcp_config_signature()
            )

            if (
                self._mcp_pool
                and self._mcp_pool_signature
                == signature
            ):

                self.mcp_servers = list(
                    self._mcp_pool
                )

            else:

                self.mcp_servers = (
                    self._build_enabled_mcp_servers()
                )

        yield {
            "event": "task_started",
            "task_id": task_id,
            "time": now_text(),
        }

        try:

            # =====================================
            # 长对话 Context 自动压缩
            # =====================================

            compaction_plan = (
                get_compaction_plan(
                    self.session_id
                )
            )

            if compaction_plan[
                "needed"
            ]:

                yield {
                    "event":
                        "context_compaction_started",

                    "task_id":
                        task_id,

                    "total_items":
                        compaction_plan[
                            "total_items"
                        ],

                    "new_summary_items":
                        compaction_plan[
                            "new_summary_items"
                        ],

                    "time":
                        now_text(),
                }

                compaction_result = (
                    await maybe_compact_context(
                        self.session_id
                    )
                )

                if compaction_result[
                    "compacted"
                ]:

                    yield {
                        "event":
                            "context_compaction_completed",

                        "task_id":
                            task_id,

                        "summarized_items":
                            compaction_result[
                                "target_summarized_items"
                            ],

                        "summary_length":
                            compaction_result[
                                "summary_length"
                            ],

                        "time":
                            now_text(),
                    }

                else:

                    yield {
                        "event":
                            "context_compaction_failed",

                        "task_id":
                            task_id,

                        "error":
                            compaction_result.get(
                                "error",
                                "",
                            ),

                        "time":
                            now_text(),
                    }

            # =====================================
            # 连接当前 Task 的 MCP Servers
            #
            # 先发出“正在连接”事件再 await：
            # npx 冷启动要 20 秒以上，
            # 如果等 connect 返回才通知界面，
            # 这段时间界面是静止的，看起来就像卡死。
            # =====================================

            for server in list(
                self.mcp_servers
            ):

                yield {
                    "event":
                        "mcp_connecting",
                    "task_id":
                        task_id,
                    "server":
                        _mcp_display_name(
                            server
                        ),
                    "time":
                        now_text(),
                }

            mcp_events = await (
                self._connect_mcp_servers(
                    reconnect=False
                )
            )

            for mcp_event in mcp_events:
                yield mcp_event

            result = None

            async for event in (
                self._run_streamed(
                    user_input
                )
            ):

                # 用户点击“停止任务”
                if (
                    self._cancel_requested
                ):

                    yield {
                        "event":
                            "task_cancelled",
                        "task_id":
                            task_id,
                        "time":
                            now_text(),
                    }

                    result = None

                    break

                if (
                    event.get("event")
                    == "_runner_finished"
                ):

                    result = event[
                        "result"
                    ]

                else:

                    yield event

            if (
                result is None
                and self._cancel_requested
            ):

                # 用户主动停止。
                # MCP 连接保留在池里复用：
                # 停止只结束当前 Task，不代表下次要重新冷启动。

                self._clear_pending()

                self._cancel_requested = (
                    False
                )

                self.running = False

                yield {
                    "status":
                        "cancelled",
                    "task_id":
                        task_id,
                    "message": (
                        "已停止当前任务。"
                    ),
                    "time": now_text(),
                }

                return

            if result is None:

                raise RuntimeError(
                    "流式 Runner "
                    "没有返回最终结果。"
                )

            terminal = (
                self._finalize_result(
                    result
                )
            )

            # 如果进入 HITL，当前 async 阶段结束前先断开 MCP，
            # 保留对象供审批恢复时重新连接。
            if (
                terminal.get("status")
                == "approval_required"
            ):

                # 进入 HITL：保持 MCP 连接不断，
                # 否则用户点「批准」后还要再等一次冷启动。

                yield terminal

                return

            # 正常结束：清 Task 状态，但保留 MCP 连接复用。

            self._cancel_requested = False

            self._clear_pending()

            yield terminal

        except Exception as e:

            # 出错时连接状态不可信，销毁连接池，
            # 下次任务重新连接。
            self._mcp_pool = []

            self._mcp_pool_signature = (
                None
            )

            await self._disconnect_mcp_servers(
                keep_for_resume=False
            )

            yield self._handle_failure(
                e
            )

        except BaseException:

            # 客户端中途断开时会走到这里：
            # 关闭标签页、网络掉线，或上层 generator
            # 没有消费完就被回收，Python 会向本 generator
            # 抛 GeneratorExit / CancelledError。
            #
            # 它们继承自 BaseException 而不是 Exception，
            # 上面的分支接不住，_running 就会永久停在 True，
            # 之后每一条新任务都会被
            # "当前已有任务正在运行" 挡在门外，
            # 除了重启服务没有任何自救办法。
            #
            # 此刻已经没办法再 yield 事件给任何人，
            # 这里不吞异常，照原样往上抛。
            raise

        finally:

            # 所有退出路径的统一兜底：
            # 正常完成 / 进入审批 / 取消 / 失败 / 断开。
            # 唯一职责是把运行位让出来，
            # 不影响 pending_state 等其它 Task 状态。
            self.running = False

    # ========================================================
    # 审批：流式恢复
    # ========================================================

    def request_cancel(
        self,
    ) -> None:
        """
        请求停止当前正在运行的 Task。

        只设置标志位；真正的退出发生在
        stream_task 的事件循环中，
        这样能保证 MCP 连接被正常 cleanup。
        """

        self._cancel_requested = True
        from agent_tools.coding_tools import cancel_active_shells
        cancel_active_shells()

    def is_busy(
        self,
    ) -> bool:
        """
        当前是否正在执行任务或等待审批。
        """

        return bool(
            self.running
            or self.pending_interruptions
        )

    async def stream_approval(
        self,
        approved: bool,
    ):
        """
        批准或拒绝当前 Tool Call，
        然后继续流式执行原任务。
        """

        with self.lock:

            if (
                self.pending_state
                is None
                or not (
                    self.pending_interruptions
                )
            ):

                yield {
                    "status": "error",
                    "message": (
                        "当前没有等待审批的操作。"
                    ),
                    "time": now_text(),
                }

                return

            task_id = self.task_id

            interruption = (
                self.pending_interruptions
                .pop(0)
            )

        set_task_context(
            self.session_id,
            task_id,
        )

        # HITL 恢复发生在新的异步调用中，
        # 重新激活原 Task 的搜索预算状态。
        activate_search_task(
            task_id
        )

        tool_name = (
            interruption.name
            or "unknown"
        )

        # ====================================================
        # 保存人工决定
        # ====================================================

        if approved:

            self.pending_state.approve(
                interruption
            )

            write_log(
                {
                    "event": (
                        "approval_approved"
                    ),
                    "tool": tool_name,
                }
            )

            yield {
                "event": (
                    "approval_approved"
                ),
                "task_id": task_id,
                "tool": tool_name,
                "time": now_text(),
            }

        else:

            self.pending_state.reject(
                interruption,
                rejection_message=(
                    "用户拒绝了这个操作。"
                ),
            )

            write_log(
                {
                    "event": (
                        "approval_rejected"
                    ),
                    "tool": tool_name,
                }
            )

            yield {
                "event": (
                    "approval_rejected"
                ),
                "task_id": task_id,
                "tool": tool_name,
                "time": now_text(),
            }

        # ====================================================
        # 同一轮还有别的审批
        # ====================================================

        if self.pending_interruptions:

            next_interruption = (
                self.pending_interruptions[0]
            )

            yield self._approval_payload(
                next_interruption
            )

            return

        # ====================================================
        # 所有审批处理完成，恢复原 Runner
        # ====================================================

        with self.lock:

            self.running = True

            self._cancel_requested = (
                False
            )

            state = self.pending_state

        try:

            # HITL 暂停时 MCP 已断开。
            # 在同一个审批恢复 async 阶段重新连接。
            mcp_events = await (
                self._connect_mcp_servers(
                    reconnect=True
                )
            )

            for mcp_event in mcp_events:
                yield mcp_event

            result = None

            async for event in (
                self._run_streamed(
                    state
                )
            ):

                if (
                    event.get("event")
                    == "_runner_finished"
                ):

                    result = event[
                        "result"
                    ]

                else:

                    yield event

            if result is None:

                raise RuntimeError(
                    "审批恢复后没有获得"
                    "最终 Runner 结果。"
                )

            terminal = (
                self._finalize_result(
                    result
                )
            )

            if (
                terminal.get("status")
                == "approval_required"
            ):

                # 进入 HITL：保持 MCP 连接不断，
                # 否则用户点「批准」后还要再等一次冷启动。

                yield terminal

                return

            await self._disconnect_mcp_servers(
                keep_for_resume=False
            )

            self._clear_pending()

            yield terminal

        except Exception as e:

            # 出错时连接状态不可信，销毁连接池，
            # 下次任务重新连接。
            self._mcp_pool = []

            self._mcp_pool_signature = (
                None
            )

            await self._disconnect_mcp_servers(
                keep_for_resume=False
            )

            yield self._handle_failure(
                e
            )

        except BaseException:

            # 客户端中途断开时会走到这里：
            # 关闭标签页、网络掉线，或上层 generator
            # 没有消费完就被回收，Python 会向本 generator
            # 抛 GeneratorExit / CancelledError。
            #
            # 它们继承自 BaseException 而不是 Exception，
            # 上面的分支接不住，_running 就会永久停在 True，
            # 之后每一条新任务都会被
            # "当前已有任务正在运行" 挡在门外，
            # 除了重启服务没有任何自救办法。
            #
            # 此刻已经没办法再 yield 事件给任何人，
            # 这里不吞异常，照原样往上抛。
            raise

        finally:

            # 所有退出路径的统一兜底：
            # 正常完成 / 进入审批 / 取消 / 失败 / 断开。
            # 唯一职责是把运行位让出来，
            # 不影响 pending_state 等其它 Task 状态。
            self.running = False

    # ========================================================
    # SDK Streaming
    # ========================================================

    async def _run_streamed(
        self,
        runner_input,
    ):
        """
        使用 Agents SDK 原生 Streaming。

        输出统一内部事件。
        """

        result = Runner.run_streamed(
            personal_agent,
            runner_input,
            session=self.session,
            max_turns=(
                get_int_setting(
                    "agent.max_turns",
                    10,
                    minimum=3,
                    maximum=40,
                )
            ),
            run_config=build_run_config(
                self.session_id
            ),
        )

        # 注意：
        # 不要在这里创建 active_calls = {}
        #
        # HITL 情况下，同一个 Task 会先暂停等待审批，
        # 然后使用 RunState 再次进入 _run_streamed()。
        #
        # 因此 Tool Call 状态必须保存在：
        #
        # self.active_calls
        #
        # 中。

        async for event in (
            result.stream_events()
        ):

            # =================================================
            # 模型文本增量
            # =================================================

            if (
                event.type
                == "raw_response_event"
            ):

                data = event.data

                event_type = getattr(
                    data,
                    "type",
                    "",
                )

                delta = getattr(
                    data,
                    "delta",
                    None,
                )

                if (
                    event_type
                    == (
                        "response."
                        "output_text.delta"
                    )
                    and isinstance(
                        delta,
                        str,
                    )
                    and delta
                ):

                    yield {
                        "event": (
                            "text_delta"
                        ),
                        "delta": delta,
                        "task_id": (
                            self.task_id
                        ),
                        "time": now_text(),
                    }

                continue

            # =================================================
            # Agent 更新
            # =================================================

            if (
                event.type
                == (
                    "agent_updated_"
                    "stream_event"
                )
            ):

                agent_name = getattr(
                    event.new_agent,
                    "name",
                    "",
                )

                yield {
                    "event": (
                        "agent_updated"
                    ),
                    "agent_name": (
                        agent_name
                    ),
                    "task_id": (
                        self.task_id
                    ),
                    "time": now_text(),
                }

                continue

            # =================================================
            # Run Item
            # =================================================

            if (
                event.type
                != (
                    "run_item_stream_event"
                )
            ):

                continue

            item = event.item

            event_name = getattr(
                event,
                "name",
                "",
            )

            # -------------------------------------------------
            # Tool Call
            # -------------------------------------------------

            if (
                event_name
                == "tool_called"
            ):

                tool_name = (
                    getattr(
                        item,
                        "tool_name",
                        None,
                    )
                    or raw_field(
                        item,
                        "name",
                    )
                    or "unknown"
                )

                call_id = (
                    getattr(
                        item,
                        "call_id",
                        None,
                    )
                    or raw_field(
                        item,
                        "call_id",
                    )
                    or raw_field(
                        item,
                        "id",
                    )
                    or str(id(item))
                )

                arguments = raw_field(
                    item,
                    "arguments",
                )

                if arguments is None:

                    arguments = (
                        raw_field(
                            item,
                            "params",
                        )
                        or raw_field(
                            item,
                            "input",
                        )
                    )

                safe_arguments = (
                    sanitize_tool_arguments(
                        tool_name,
                        arguments,
                    )
                )

                self.active_calls[
                    str(call_id)
                ] = {
                    "tool": tool_name,
                    "started": (
                        perf_counter()
                    ),
                    "arguments": (
                        safe_arguments
                    ),
                }

                yield {
                    "event": (
                        "tool_started"
                    ),
                    "task_id": (
                        self.task_id
                    ),
                    "call_id": str(
                        call_id
                    ),
                    "tool": tool_name,
                    "arguments": (
                        safe_arguments
                    ),
                    "time": now_text(),
                }

                continue

            # -------------------------------------------------
            # Tool Output
            # -------------------------------------------------

            if (
                event_name
                == "tool_output"
            ):

                call_id = (
                    getattr(
                        item,
                        "call_id",
                        None,
                    )
                    or raw_field(
                        item,
                        "call_id",
                    )
                    or ""
                )

                # 使用实例级 active_calls，
                # 这样 HITL 审批恢复后仍然可以找到
                # 审批前的 Tool Call 信息。
                info = self.active_calls.get(
                    str(call_id),
                    {},
                )

                tool_name = info.get(
                    "tool",
                    "unknown",
                )

                started = info.get(
                    "started"
                )

                duration_ms = ""

                if started is not None:

                    duration_ms = round(
                        (
                            perf_counter()
                            - started
                        )
                        * 1000,
                        2,
                    )

                output = getattr(
                    item,
                    "output",
                    "",
                )

                (
                    is_error,
                    display_output,
                ) = classify_tool_output(
                    tool_name,
                    output,
                )

                yield {
                    "event": (
                        "tool_failed"
                        if is_error
                        else "tool_completed"
                    ),
                    "task_id": (
                        self.task_id
                    ),
                    "call_id": str(
                        call_id
                    ),
                    "tool": tool_name,
                    "duration_ms": (
                        duration_ms
                    ),
                    "output": (
                        display_output[:1000]
                    ),
                    "time": now_text(),
                }

                # Tool 已结束，不再需要继续保存。
                self.active_calls.pop(
                    str(call_id),
                    None,
                )

                continue

        # 必须把 stream_events() 完整消费结束后，
        # interruptions / session / final_output 等状态
        # 才是稳定的。
        yield {
            "event": "_runner_finished",
            "result": result,
        }

    # ========================================================
    # Run 完成处理
    # ========================================================

    def _finalize_result(
        self,
        result,
    ) -> dict:

        # ====================================================
        # 等待审批
        # ====================================================

        if result.interruptions:

            self.pending_state = (
                result.to_state()
            )

            self.pending_interruptions = (
                list(
                    result.interruptions
                )
            )

            self.running = False

            for interruption in (
                self.pending_interruptions
            ):

                write_log(
                    {
                        "event": (
                            "approval_requested"
                        ),
                        "tool": (
                            interruption.name
                        ),
                        "arguments": (
                            sanitize_tool_arguments(
                                interruption.name,
                                interruption.arguments,
                            )
                        ),
                    }
                )

            return (
                self._approval_payload(
                    self.pending_interruptions[
                        0
                    ]
                )
            )

        # ====================================================
        # 正常完成
        # ====================================================

        final_output = (
            result.final_output
            if (
                result.final_output
                is not None
            )
            else "任务已完成。"
        )

        write_log(
            {
                "event": (
                    "task_completed"
                ),
                "output_length": len(
                    str(
                        final_output
                    )
                ),
            }
        )

        task_id = self.task_id

        # 在清理 Task 搜索状态之前先保存 Sources，
        # 这样 GUI / CLI 可以拿到本轮真实使用过的来源。
        sources = (
            get_search_task_sources(
                task_id
            )
            if task_id
            else []
        )

        return {
            "status": "completed",
            "task_id": task_id,
            "message": str(
                final_output
            ),
            "sources": sources,
            "time": now_text(),
        }

    # ========================================================
    # Approval Payload
    # ========================================================

    def _approval_payload(
        self,
        interruption,
    ) -> dict:

        return {
            "status": (
                "approval_required"
            ),
            "task_id": (
                self.task_id
            ),
            "tool": (
                interruption.name
            ),
            "arguments": (
                interruption.arguments
            ),
            "remaining": len(
                self.pending_interruptions
            ),
            "time": now_text(),
        }

    # ========================================================
    # Error
    # ========================================================

    def _handle_failure(
        self,
        error: Exception,
    ) -> dict:

        try:

            if self.task_id:

                set_task_context(
                    self.session_id,
                    self.task_id,
                )

                write_log(
                    {
                        "event": (
                            "task_failed"
                        ),
                        "error_type": (
                            type(
                                error
                            ).__name__
                        ),
                        "error": str(
                            error
                        )[:1000],
                    }
                )

        except Exception:

            pass

        task_id = self.task_id

        self._clear_pending()

        return {
            "status": "error",
            "task_id": task_id,
            "message": (
                f"{type(error).__name__}: "
                f"{error}"
            ),
            "time": now_text(),
        }

    # ========================================================
    # Clear State
    # ========================================================

    def _clear_pending(
        self,
    ) -> None:

        task_id = self.task_id

        self.pending_state = None

        self.pending_interruptions = []

        self.active_calls = {}

        # MCP 连接本身必须在 async caller 中 cleanup。
        # 这里仅清空 Task 级引用。
        self._reset_mcp_task_state()

        # Task 真正结束时清理联网搜索预算与去重状态。
        if task_id:
            end_search_task(
                task_id
            )

        self.task_id = None

        self.running = False
