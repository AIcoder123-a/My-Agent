"""会话内自动放行与审批恢复链路的回归测试。

覆盖三段曾经各写一遍、改一处漏两处的逻辑：
- 命中放行名单的审批被自动放行，不再打断用户
- 不在名单里的审批仍然交回人工
- 放行后若又出现新的未放行审批，能正确停下
"""
import asyncio
import unittest
from threading import RLock
from unittest.mock import patch

from agent_service import AgentService


class FakeState:
    def __init__(self):
        self.approved = []
        self.rejected = []

    def approve(self, interruption):
        self.approved.append(interruption)

    def reject(self, interruption, rejection_message=None):
        self.rejected.append(interruption)


class FakeInterruption:
    def __init__(self, name):
        self.name = name
        self.arguments = {}


def make_service():
    """绕过 __init__ 构造一个只带必要字段的 AgentService。"""
    service = AgentService.__new__(AgentService)
    service.lock = RLock()
    service.session_id = 'session'
    service.task_id = 'task'
    service.pending_state = FakeState()
    service.pending_interruptions = []
    service.active_calls = {}
    service.running = False
    service._cancel_requested = False
    service._auto_approve_tools = set()
    service._mcp_pool = []
    service._mcp_pool_signature = None
    service._mcp_failed = {}
    service._mcp_failed_signature = None
    service.mcp_servers = []

    async def _connect(reconnect=False):
        return []

    async def _disconnect(*, keep_for_resume):
        return None

    async def _run_streamed(state):
        yield {'event': 'progress'}
        yield {'event': '_runner_finished', 'result': object()}

    service._connect_mcp_servers = _connect
    service._disconnect_mcp_servers = _disconnect
    service._run_streamed = _run_streamed
    service._clear_pending = lambda: None
    return service


class AutoApprovalTests(unittest.TestCase):
    def drain(self, coro):
        async def collect():
            return [item async for item in coro]
        return asyncio.run(collect())

    def test_auto_approve_skips_prompt(self):
        service = make_service()
        first = FakeInterruption('write_file')
        service.pending_interruptions = [first]
        service.set_auto_approve('write_file', True)

        service._finalize_result = lambda result: {'status': 'completed'}

        sink = {}
        with patch('agent_service.write_log'):
            events = self.drain(
                service._resume_after_approval('task', sink)
            )

        self.assertEqual([first], service.pending_state.approved)
        self.assertIsNone(sink.get('terminal'))
        self.assertEqual('completed', events[-1].get('status'))
        self.assertTrue(
            any(e.get('event') == 'approval_auto_approved' for e in events)
        )

    def test_unlisted_tool_still_prompts(self):
        service = make_service()
        first = FakeInterruption('run_shell')
        service.pending_interruptions = [first]

        sink = {}
        with patch('agent_service.write_log'):
            events = self.drain(
                service._resume_after_approval('task', sink)
            )

        self.assertEqual([], service.pending_state.approved)
        self.assertEqual([], events)
        self.assertIsNotNone(sink.get('terminal'))
        self.assertEqual('approval_required', sink['terminal']['status'])
        self.assertEqual('run_shell', sink['terminal']['tool'])

    def test_stops_at_new_unlisted_approval(self):
        """放行 write_file 之后又来一个 run_shell，必须停下问人。"""
        service = make_service()
        service.pending_interruptions = [FakeInterruption('write_file')]
        service.set_auto_approve('write_file', True)

        second = FakeInterruption('run_shell')
        calls = {'n': 0}

        def finalize(result):
            calls['n'] += 1
            if calls['n'] == 1:
                service.pending_interruptions = [second]
                return {'status': 'approval_required'}
            return {'status': 'completed'}

        service._finalize_result = finalize

        sink = {}
        with patch('agent_service.write_log'):
            self.drain(service._resume_after_approval('task', sink))

        self.assertIsNotNone(sink.get('terminal'))
        self.assertEqual('run_shell', sink['terminal']['tool'])

    def test_cancel_during_auto_approved_run(self):
        """自动放行后的执行段必须能响应停止。"""
        service = make_service()
        service.pending_interruptions = [FakeInterruption('write_file')]
        service.set_auto_approve('write_file', True)

        async def _run_streamed(state):
            service._cancel_requested = True
            yield {'event': 'progress'}
            yield {'event': '_runner_finished', 'result': object()}

        service._run_streamed = _run_streamed
        service._finalize_result = lambda result: {'status': 'completed'}

        sink = {}
        with patch('agent_service.write_log'):
            events = self.drain(
                service._resume_after_approval('task', sink)
            )

        self.assertEqual('cancelled', events[-1].get('status'))

    def test_set_auto_approve_toggle(self):
        service = make_service()
        service.set_auto_approve('write_file', True)
        self.assertEqual(['write_file'], service.auto_approve_tools())
        service.set_auto_approve('write_file', False)
        self.assertEqual([], service.auto_approve_tools())


class StreamApprovalTests(unittest.TestCase):
    """GUI 批准/拒绝按钮直连 stream_approval 的回归测试。

    曾经的故障：签名只收 approved，方法体却在用 remember，
    GUI 以 remember= 关键字调用即抛 TypeError，
    审批主链路 100% 崩溃且无测试覆盖。
    """

    def drain(self, coro):
        async def collect():
            return [item async for item in coro]
        return asyncio.run(collect())

    def setUp(self):
        self.patches = [
            patch('agent_service.write_log'),
            patch('agent_service.set_task_context'),
            patch('agent_service.activate_search_task'),
        ]
        self.write_log = self.patches[0].start()
        self.patches[1].start()
        self.patches[2].start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _audit_events(self):
        return [call[0][0].get('event') for call in self.write_log.call_args_list]

    def _attach_resume(self, service, terminal=None):
        async def _resume(task_id, sink):
            yield {'event': 'progress'}
            if terminal is not None:
                sink['terminal'] = terminal
        service._resume_after_approval = _resume

    def test_approve_with_remember_kwarg(self):
        service = make_service()
        first = FakeInterruption('run_shell')
        service.pending_interruptions = [first]
        self._attach_resume(service, terminal={'status': 'completed'})

        events = self.drain(
            service.stream_approval(approved=True, remember=True)
        )

        self.assertEqual([first], service.pending_state.approved)
        self.assertEqual(['run_shell'], service.auto_approve_tools())
        # remember 的区别只进审计日志，UI 事件流统一是 approval_approved
        self.assertIn('approval_approved_remembered', self._audit_events())
        self.assertEqual('completed', events[-1].get('status'))

    def test_approve_without_remember(self):
        """CLI 路径只传 approved，默认不进放行名单。"""
        service = make_service()
        first = FakeInterruption('run_shell')
        service.pending_interruptions = [first]
        self._attach_resume(service, terminal={'status': 'completed'})

        events = self.drain(service.stream_approval(approved=True))

        self.assertEqual([first], service.pending_state.approved)
        self.assertEqual([], service.auto_approve_tools())
        self.assertTrue(
            any(e.get('event') == 'approval_approved' for e in events)
        )

    def test_reject(self):
        service = make_service()
        first = FakeInterruption('run_shell')
        service.pending_interruptions = [first]
        self._attach_resume(service, terminal={'status': 'completed'})

        events = self.drain(
            service.stream_approval(approved=False, remember=True)
        )

        self.assertEqual([first], service.pending_state.rejected)
        self.assertEqual([], service.auto_approve_tools())
        self.assertTrue(
            any(e.get('event') == 'approval_rejected' for e in events)
        )

    def test_remember_does_not_leak_to_rejected_path(self):
        """拒绝时即便勾了记住，也不能把工具加进放行名单。"""
        service = make_service()
        service.pending_interruptions = [FakeInterruption('run_shell')]
        self._attach_resume(service)

        self.drain(
            service.stream_approval(approved=False, remember=True)
        )

        self.assertEqual([], service.auto_approve_tools())

    def test_init_bootstraps_auto_approve_tools(self):
        """回归：构造路径不经过 new_session，名单必须在 __init__ 就位。"""
        with patch(
            'agent_service.load_or_create_session',
            return_value=(None, 'bootstrap'),
        ):
            service = AgentService()

        self.assertEqual(set(), service._auto_approve_tools)
        service.set_auto_approve('run_shell', True)
        self.assertEqual(['run_shell'], service.auto_approve_tools())


if __name__ == '__main__':
    unittest.main()
