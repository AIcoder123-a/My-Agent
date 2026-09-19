import asyncio
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
)

from tool_logging import (
    start_task_context,
    set_task_context,
    write_log,
    sanitize_tool_arguments,
)


# ============================================================
# Runner 配置
# ============================================================

RUN_CONFIG = RunConfig(
    tool_not_found_behavior="return_error_to_model",
)


def now_text() -> str:
    """
    返回用于界面显示的当前时间。
    """

    return datetime.now().strftime(
        "%H:%M:%S"
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

        self.lock = RLock()

    # ========================================================
    # 基础状态
    # ========================================================

    def get_session_id(self) -> str:

        return self.session_id

    def get_task_id(
        self,
    ) -> str | None:

        return self.task_id

    def new_session(self) -> str:
        """
        创建全新的 Conversation Session。
        """

        with self.lock:

            self.session, self.session_id = (
                create_new_session()
            )

            self.task_id = None

            self.pending_state = None

            self.pending_interruptions = []

            self.active_calls = {}

            self.running = False

            return self.session_id

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

            # 新 Task 开始时清空旧 Tool Call 信息。
            self.active_calls = {}

            self.task_id = (
                start_task_context(
                    self.session_id
                )
            )

            task_id = self.task_id

        yield {
            "event": "task_started",
            "task_id": task_id,
            "time": now_text(),
        }

        try:

            result = None

            async for event in (
                self._run_streamed(
                    user_input
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
                    "流式 Runner "
                    "没有返回最终结果。"
                )

            yield self._finalize_result(
                result
            )

        except Exception as e:

            yield self._handle_failure(
                e
            )

    # ========================================================
    # 审批：流式恢复
    # ========================================================

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

            state = self.pending_state

        try:

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

            yield self._finalize_result(
                result
            )

        except Exception as e:

            yield self._handle_failure(
                e
            )

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
            max_turns=10,
            run_config=RUN_CONFIG,
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

                output_text = str(
                    output
                )

                is_error = (
                    output_text
                    .strip()
                    .startswith(
                        "TOOL_ERROR:"
                    )
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
                        output_text[:1000]
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

        self._clear_pending()

        return {
            "status": "completed",
            "task_id": task_id,
            "message": str(
                final_output
            ),
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

        self.pending_state = None

        self.pending_interruptions = []

        self.active_calls = {}

        self.task_id = None

        self.running = False