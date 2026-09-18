from threading import RLock

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
)


RUN_CONFIG = RunConfig(
    tool_not_found_behavior=(
        "return_error_to_model"
    )
)


class AgentService:
    """
    Agent 的统一运行层。

    CLI 和 GUI 都通过这里运行 Agent，
    避免复制 Runner / Session / HITL 逻辑。
    """

    def __init__(self):

        self.session, self.session_id = (
            load_or_create_session()
        )

        self.task_id = None

        self.pending_state = None
        self.pending_interruptions = []

        self.lock = RLock()

    # =====================================
    # Session
    # =====================================

    def get_session_id(self) -> str:
        return self.session_id

    def new_session(self) -> str:
        """
        创建新 Session。

        同时放弃当前尚未处理的审批任务。
        """

        with self.lock:

            self.session, self.session_id = (
                create_new_session()
            )

            self.task_id = None
            self.pending_state = None
            self.pending_interruptions = []

            return self.session_id

    # =====================================
    # 新用户任务
    # =====================================

    def start_task(
        self,
        user_input: str,
    ) -> dict:

        with self.lock:

            user_input = user_input.strip()

            if not user_input:
                return {
                    "status": "error",
                    "message": "用户输入不能为空。",
                }

            # 如果还有待审批任务，
            # 不允许同时启动另一个任务。
            if self.pending_state is not None:
                return {
                    "status": "error",
                    "message": (
                        "当前还有等待审批的工具调用。"
                        "请先批准或拒绝该操作。"
                    ),
                }

            self.task_id = start_task_context(
                self.session_id
            )

            try:

                result = Runner.run_sync(
                    personal_agent,
                    user_input,
                    session=self.session,
                    max_turns=10,
                    run_config=RUN_CONFIG,
                )

                return self._process_result(
                    result
                )

            except Exception as e:

                write_log(
                    {
                        "event": "task_failed",
                        "error_type": (
                            type(e).__name__
                        ),
                        "error": str(e)[:1000],
                    }
                )

                self._clear_pending()

                return {
                    "status": "error",
                    "message": (
                        f"{type(e).__name__}: {e}"
                    ),
                }

    # =====================================
    # HITL 审批
    # =====================================

    def approve_current(self) -> dict:
        """
        批准当前等待中的 Tool Call。
        """

        return self._resolve_current(
            approved=True
        )

    def reject_current(self) -> dict:
        """
        拒绝当前等待中的 Tool Call。
        """

        return self._resolve_current(
            approved=False
        )

    def _resolve_current(
        self,
        approved: bool,
    ) -> dict:

        with self.lock:

            if (
                self.pending_state is None
                or not self.pending_interruptions
            ):
                return {
                    "status": "error",
                    "message": (
                        "当前没有等待审批的操作。"
                    ),
                }

            # GUI 点击审批时重新恢复日志上下文
            set_task_context(
                self.session_id,
                self.task_id,
            )

            interruption = (
                self.pending_interruptions[0]
            )

            if approved:

                self.pending_state.approve(
                    interruption
                )

                write_log(
                    {
                        "event": (
                            "approval_approved"
                        ),
                        "tool": (
                            interruption.name
                        ),
                    }
                )

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
                        "tool": (
                            interruption.name
                        ),
                    }
                )

            try:

                # 从刚才暂停的位置恢复
                result = Runner.run_sync(
                    personal_agent,
                    self.pending_state,
                    session=self.session,
                    run_config=RUN_CONFIG,
                )

                return self._process_result(
                    result
                )

            except Exception as e:

                write_log(
                    {
                        "event": "task_failed",
                        "error_type": (
                            type(e).__name__
                        ),
                        "error": str(e)[:1000],
                    }
                )

                self._clear_pending()

                return {
                    "status": "error",
                    "message": (
                        f"{type(e).__name__}: {e}"
                    ),
                }

    # =====================================
    # Runner Result
    # =====================================

    def _process_result(
        self,
        result,
    ) -> dict:

        # ---------- 需要人工审批 ----------

        if result.interruptions:

            self.pending_state = (
                result.to_state()
            )

            self.pending_interruptions = list(
                result.interruptions
            )

            interruption = (
                self.pending_interruptions[0]
            )

            write_log(
                {
                    "event": (
                        "approval_requested"
                    ),
                    "tool": (
                        interruption.name
                    ),
                    "arguments": (
                        interruption.arguments
                    ),
                }
            )

            return {
                "status": "approval_required",
                "task_id": self.task_id,
                "tool": interruption.name,
                "arguments": (
                    interruption.arguments
                ),
                "remaining": len(
                    self.pending_interruptions
                ),
            }

        # ---------- 正常完成 ----------

        final_output = (
            result.final_output
            if result.final_output is not None
            else "任务已完成。"
        )

        write_log(
            {
                "event": "task_completed",
            }
        )

        task_id = self.task_id

        self._clear_pending()

        return {
            "status": "completed",
            "task_id": task_id,
            "message": str(final_output),
        }

    def _clear_pending(self):

        self.pending_state = None
        self.pending_interruptions = []
        self.task_id = None