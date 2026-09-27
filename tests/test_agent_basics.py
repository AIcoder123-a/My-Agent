import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from agent_tools import coding_tools as coding
from agent_tools import file_tools
from workspace_io import import_attachments, stage_download


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.patches = [patch.object(file_tools, 'WORKSPACE_DIR', self.root),
                        patch('paths.WORKSPACE_DIR', self.root),
                        patch('workspace_io.WORKSPACE_DIR', self.root),
                        patch('workspace_io.DATA_DIR', Path(self.temp.name) / 'data')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        try:
            self.temp.cleanup()
        except OSError:
            # Windows 上文件句柄释放存在短暂延迟（杀软 / 索引服务
            # 扫描刚写入的大文件），立即清理偶发 WinError 145，
            # 稍候重试；仍失败则留给系统临时目录清理兜底。
            time.sleep(0.3)
            try:
                self.temp.cleanup()
            except OSError:
                pass

    def test_path_escape(self):
        with self.assertRaises(ValueError):
            file_tools.safe_workspace_path('../secret')
        with self.assertRaises(ValueError):
            stage_download('../secret')

    def test_exact_edit_preserves_crlf(self):
        target = self.root / 'code.py'
        target.write_bytes(b'a = 1\r\nb = 2\r\n')
        coding.edit_file_impl('code.py', 'a = 1\nb = 2', 'a = 3\nb = 4')
        self.assertEqual(target.read_bytes(), b'a = 3\r\nb = 4\r\n')

    def test_ambiguous_edit_does_not_change_file(self):
        target = self.root / 'code.py'
        target.write_text('same\nsame', encoding='utf-8')
        with self.assertRaises(ValueError):
            coding.edit_file_impl('code.py', 'same', 'other')
        self.assertEqual(target.read_text(), 'same\nsame')

    def test_search_bounded_and_skips_dependencies(self):
        (self.root / 'a.txt').write_text('hello\nHELLO\nhello', encoding='utf-8')
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules' / 'ignored.txt').write_text('hello', encoding='utf-8')
        result = json.loads(coding.search_workspace_impl('hello', max_results=2))
        self.assertTrue(result['truncated'])
        self.assertEqual([m['line'] for m in result['matches']], [1, 2])
        self.assertEqual(len(json.loads(coding.search_workspace_impl('hello'))['matches']), 3)

    def test_import_download_and_duplicates(self):
        source = Path(self.temp.name) / 'source.txt'
        source.write_text('attachment', encoding='utf-8')
        names = import_attachments([str(source), str(source)])
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(Path(stage_download(names[0])).read_text(), 'attachment')

    def test_attachment_limit_is_atomic(self):
        source = Path(self.temp.name) / 'huge.txt'
        with source.open('wb') as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            import_attachments([str(source)])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_shell_success_and_nonzero(self):
        result = json.loads(asyncio.run(coding.run_shell_impl("Write-Output '小智 OK'; exit 7")))
        self.assertEqual(result['exit_code'], 7)
        self.assertIn('小智 OK', result['output'])
        self.assertEqual(result['status'], 'completed')

    def test_shell_timeout(self):
        result = json.loads(asyncio.run(coding.run_shell_impl('Start-Sleep -Seconds 20', timeout_seconds=1)))
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(coding._active, {})

    def test_shell_cancel(self):
        async def run():
            task = asyncio.create_task(coding.run_shell_impl('Start-Sleep -Seconds 20'))
            for _ in range(100):
                if coding._active:
                    break
                await asyncio.sleep(.02)
            coding.cancel_active_shells()
            return json.loads(await task)
        self.assertEqual(asyncio.run(run())['status'], 'cancelled')
        self.assertEqual(coding._active, {})

    def test_shell_secret_environment_removed(self):
        with patch.dict('os.environ', {'TEST_API_KEY': 'never-show-this'}):
            result = json.loads(asyncio.run(coding.run_shell_impl('Write-Output "key=$env:TEST_API_KEY"')))
        self.assertNotIn('never-show-this', result['output'])

    def test_mutating_tools_require_approval(self):
        from agent_tools.mcp_content import read_mcp_resource, get_mcp_prompt
        for item in (coding.edit_file, coding.run_shell, read_mcp_resource, get_mcp_prompt):
            self.assertIs(item.needs_approval, True)

    def test_new_session_cannot_discard_running_task_or_approval(self):
        from agent_service import AgentService
        from threading import RLock
        service = AgentService.__new__(AgentService)
        service.lock = RLock()
        service.session_id = 'keep-current-session'
        with patch('agent_service.create_new_session') as create:
            for running, pending in ((True, []), (False, [object()])):
                service.running = running
                service.pending_interruptions = pending
                with self.assertRaises(RuntimeError):
                    service.new_session()
                self.assertEqual(service.session_id, 'keep-current-session')
            create.assert_not_called()

    def test_sdk_approval_prevents_execution_until_approved(self):
        from agents import Agent, Model, ModelResponse, Runner, RunConfig, Usage
        from openai.types.responses import ResponseFunctionToolCall

        class CommandModel(Model):
            async def get_response(self, *args, **kwargs):
                return ModelResponse(output=[ResponseFunctionToolCall(
                    id='fc_test', call_id='call_test', type='function_call', name='run_shell',
                    arguments=json.dumps({'command': 'Write-Output approved', 'cwd': '.', 'timeout_seconds': 10}))],
                    usage=Usage(), response_id='response_test')

            async def stream_response(self, *args, **kwargs):
                raise NotImplementedError
                yield

        async def run():
            agent = Agent(name='test', model=CommandModel(), tools=[coding.run_shell], tool_use_behavior='stop_on_first_tool')
            config = RunConfig(tracing_disabled=True)
            with patch.object(coding, 'run_shell_impl', new_callable=AsyncMock, return_value='approved') as execute:
                pending = await Runner.run(agent, 'execute', run_config=config)
                self.assertEqual(len(pending.interruptions), 1)
                execute.assert_not_awaited()
                state = pending.to_state()
                state.approve(pending.interruptions[0])
                result = await Runner.run(agent, state, run_config=config)
                execute.assert_awaited_once_with('Write-Output approved', '.', 10)
                self.assertEqual(result.final_output, 'approved')
        asyncio.run(run())

    def test_plan_persists_for_session_and_validates_statuses(self):
        from agent_tools import planning_tools as planning
        from tool_logging import current_session_id, current_task_id
        from agents.tool_context import ToolContext
        session_token = current_session_id.set('test-session')
        task_token = current_task_id.set('test-task')
        try:
            with patch.object(planning, 'DATA_DIR', Path(self.temp.name)):
                result = asyncio.run(planning.update_plan.on_invoke_tool(ToolContext(context=None, tool_name='test', tool_call_id='test-call', tool_arguments='{}'), json.dumps({'steps': [
                    {'title': 'Inspect', 'status': 'completed'}, {'title': 'Verify', 'status': 'in_progress'}]})))
                self.assertEqual(json.loads(result)['task_id'], 'test-task')
                self.assertTrue(planning.plan_path('test-session').is_file())
                with self.assertRaises(ValueError):
                    planning.plan_path('../../escape')
        finally:
            current_session_id.reset(session_token)
            current_task_id.reset(task_token)

    def test_mcp_resource_routes_to_connected_server(self):
        from agent_tools import mcp_content
        from agents.tool_context import ToolContext
        from types import SimpleNamespace
        server = SimpleNamespace(name='test-mcp', read_resource=AsyncMock(return_value={'contents': [{'text': 'resource', 'blob': 'secret-binary'}]}))
        with patch.object(mcp_content, 'connected_servers', return_value=[server]):
            result = asyncio.run(mcp_content.read_mcp_resource.on_invoke_tool(
                ToolContext(context=None, tool_name='test', tool_call_id='test-call', tool_arguments='{}'), json.dumps({'server_name': 'test-mcp', 'uri': 'test://resource'})))
            server.read_resource.assert_awaited_once_with('test://resource')
            self.assertNotIn('secret-binary', result)
            with self.assertRaises(ValueError):
                mcp_content.find_server('disconnected')


if __name__ == '__main__':
    unittest.main()
