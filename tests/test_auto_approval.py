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


if __name__ == '__main__':
    unittest.main()
