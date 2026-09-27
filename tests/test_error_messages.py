"""错误呈现与放弃审批路径的回归测试。

背景：_handle_failure 曾把原始异常文本直接甩给用户
（内部路径、base_url 都会暴露），常见网络 / 认证错误
现在映射成可操作的中文提示；等待审批时用户除了
「拒绝（交回模型继续跑）」还可以「放弃任务」直接结束。
"""
import asyncio
import unittest
from threading import RLock
from unittest.mock import patch

import httpx
import openai
from agents import MaxTurnsExceeded

from agent_service import AgentService


def bare_service():
    """_friendly_error_message 不依赖实例状态，绕过 __init__ 构造。"""
    return AgentService.__new__(AgentService)


def api_request():
    return httpx.Request("POST", "https://api.example.com/v1/chat")


class FriendlyErrorTests(unittest.TestCase):
    def setUp(self):
        self.service = bare_service()

    def test_timeout_is_actionable(self):
        message = self.service._friendly_error_message(
            openai.APITimeoutError(request=api_request())
        )
        self.assertIn("超时", message)
        self.assertNotIn("APITimeoutError", message)

    def test_connection_error_is_actionable(self):
        message = self.service._friendly_error_message(
            openai.APIConnectionError(request=api_request())
        )
        self.assertIn("无法连接", message)
        self.assertNotIn("APIConnectionError", message)

    def test_auth_error_points_to_settings(self):
        response = httpx.Response(401, request=api_request())
        message = self.service._friendly_error_message(
            openai.AuthenticationError(
                "Invalid API key", response=response, body=None
            )
        )
        self.assertIn("API Key", message)
        self.assertNotIn("AuthenticationError", message)

    def test_rate_limit_is_actionable(self):
        response = httpx.Response(429, request=api_request())
        message = self.service._friendly_error_message(
            openai.RateLimitError(
                "Too many requests", response=response, body=None
            )
        )
        self.assertIn("限流", message)

    def test_max_turns_explains_and_suggests(self):
        message = self.service._friendly_error_message(
            MaxTurnsExceeded("Max turns (10) exceeded")
        )
        self.assertIn("步数", message)
        self.assertNotIn("MaxTurnsExceeded", message)

    def test_unknown_error_keeps_detail_truncated(self):
        class WeirdError(Exception):
            pass

        message = self.service._friendly_error_message(
            WeirdError("x" * 1000)
        )
        self.assertTrue(message.startswith("WeirdError:"))
        self.assertLessEqual(len(message), 320)


class AbandonPendingTests(unittest.TestCase):
    """等待审批时的「放弃」路径。"""

    def _make_service(self):
        from tests.test_auto_approval import make_service

        service = make_service()

        def clear_pending():
            service.pending_state = None
            service.pending_interruptions = []
            service.task_id = None
            service.running = False

        service._clear_pending = clear_pending
        return service

    def test_abandon_clears_pending_and_disconnects(self):
        service = self._make_service()
        service.pending_interruptions = [
            type("I", (), {"name": "run_shell"})()
        ]
        disconnected = []

        async def _disconnect(*, keep_for_resume):
            disconnected.append(keep_for_resume)

        service._disconnect_mcp_servers = _disconnect

        with patch("agent_service.write_log"):
            message = asyncio.run(service.abandon_pending_approval())

        self.assertIn("已放弃", message)
        self.assertEqual([], service.pending_interruptions)
        self.assertIsNone(service.pending_state)
        self.assertEqual([False], disconnected)

    def test_abandon_without_pending_is_noop(self):
        service = self._make_service()
        with patch("agent_service.write_log"):
            message = asyncio.run(service.abandon_pending_approval())
        self.assertIn("没有等待审批", message)

    def test_cli_teardown_clears_pool(self):
        """CLI 每次调用新建事件循环，池子必须随任务拆掉。"""
        service = self._make_service()
        service._mcp_pool = [object()]
        service._mcp_pool_signature = "sig"
        service.mcp_servers = [object()]
        calls = []

        async def _disconnect(*, keep_for_resume):
            calls.append(keep_for_resume)

        service._disconnect_mcp_servers = _disconnect
        service._teardown_mcp_pool_for_cli()

        self.assertEqual([], service._mcp_pool)
        self.assertIsNone(service._mcp_pool_signature)
        self.assertEqual([False], calls)


if __name__ == "__main__":
    unittest.main()
