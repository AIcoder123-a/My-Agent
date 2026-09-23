"""Exercise chat callbacks without opening the user's database or model connection."""
import ast
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_functions(filename, names, namespace):
    """从给定文件里挑出需要的函数并注入 namespace。

    filename 可以是候选列表：界面拆分后这些函数搬到了 gui_handlers.py，
    按顺序找，避免以后再挪位置时测试全崩。
    """
    candidates = [filename] if isinstance(filename, str) else list(filename)
    for name in candidates:
        path = ROOT / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in names]
        if len(nodes) == len(names):
            exec(compile(ast.Module(body=nodes, type_ignores=[]), name, "exec"), namespace)
            return namespace
    raise AssertionError(f"{sorted(names)} not found in {candidates}")


class ChatRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = SimpleNamespace(is_busy=Mock(return_value=False), get_session_id=lambda: "test",
                                       switch_conversation=AsyncMock(side_effect=RuntimeError("busy")))
        self.ns = dict(Any=Any, service=self.service,
                       SEND_OUTPUT_COUNT=14, SESSION_SWITCH_OUTPUT_COUNT=13,
                       gr=SimpleNamespace(skip=lambda: {"__type__": "update"}, Warning=Mock()),
                       approval_view=lambda *args: {}, approval_button_state=lambda value: value,
                       button_state=lambda value: value, current_task_text=lambda: "task",
                       empty_sources_text=lambda: "", conversation_dropdown_state=lambda: "original",
                       tool_text=lambda value: value or "tool", short_text=str, json_text=str,
                       render_sources_markdown=lambda value: "", perf_counter=Mock(side_effect=range(1000)))
        load_functions(["gui_handlers.py", "gui.py"],
                       {"clone_history", "normalize_agent_text", "progress_message", "add_trace",
                        "render_service_stream", "send_task", "switch_conversation_ui",
                        "skip_tuple"}, self.ns)
        load_functions("memory.py", {"_content_to_text", "chat_messages_from_items"}, self.ns)

    async def render(self, events, history=None):
        async def stream():
            for event in events:
                yield event
        return [copy.deepcopy(frame) async for frame in self.ns["render_service_stream"](
            stream(), history or [{"role": "user", "content": "question"}], [])]

    async def test_long_progress_is_replaced_by_final_answer(self):
        frames = await self.render([
            {"event": "text_delta", "delta": "Planning. " * 100},
            {"event": "tool_started", "tool": "finish_task"},
            {"status": "completed", "message": "Actual answer"},
        ])
        self.assertEqual(frames[0][0][-1]["metadata"]["status"], "pending")
        self.assertEqual(frames[-1][0], [{"role": "user", "content": "question"},
                                       {"role": "assistant", "content": "Actual answer"}])

    async def test_completed_text_wins_over_partial_deltas(self):
        frames = await self.render([{"event": "text_delta", "delta": "Half"},
                                    {"status": "completed", "message": "Whole answer"}])
        self.assertEqual(frames[-1][0][-1]["content"], "Whole answer")

    async def test_approval_resume_does_not_merge_previous_progress(self):
        history = [{"role": "user", "content": "previous"}, {"role": "assistant", "content": "old answer"},
                   {"role": "user", "content": "new"}, self.ns["progress_message"]("Waiting for approval")]
        frames = await self.render([{"event": "text_delta", "delta": "Working"},
                                    {"status": "completed", "message": "new answer"}], history)
        self.assertEqual(frames[-1][0][:2], history[:2])
        self.assertEqual(len(frames[-1][0]), 4)
        self.assertEqual(frames[-1][0][-1]["content"], "new answer")

    async def test_stop_finishes_progress_indicator(self):
        frames = await self.render([{"event": "text_delta", "delta": "Partial"}, {"status": "cancelled"}])
        self.assertEqual(frames[-1][0][-1]["metadata"]["status"], "done")
        self.assertFalse(frames[-1][12])

    def test_history_restores_finish_tool_answer_not_commentary(self):
        items = [{"role": "user", "content": "question"}, {"role": "assistant", "content": "I will look"},
                 {"type": "function_call", "name": "finish_task", "call_id": "finish"},
                 {"type": "function_call_output", "call_id": "unrelated", "output": "not an answer"},
                 {"type": "function_call_output", "call_id": "finish", "output": "Actual answer"},
                 {"role": "user", "content": "next"}, {"role": "assistant", "content": "Next answer"}]
        history = self.ns["chat_messages_from_items"](items)
        self.assertEqual([m["content"] for m in history], ["question", "Actual answer", "next", "Next answer"])

    def test_short_markdown_lines_and_code_are_preserved(self):
        text = "```\n" + "\n".join(str(i) for i in range(20)) + "\n```"
        self.assertEqual(self.ns["normalize_agent_text"](text), text)

    async def test_busy_send_preserves_all_ui_state(self):
        self.service.is_busy.return_value = True
        frames = [frame async for frame in self.ns["send_task"]("draft", [], [])]
        self.assertEqual(frames, [tuple({"__type__": "update"} for _ in range(14))])

    async def test_stream_clears_submitted_text_once_and_preserves_new_draft(self):
        async def stream(message):
            yield {"event": "text_delta", "delta": "Answer"}
            yield {"status": "completed", "message": "Final"}
        self.service.stream_task = stream
        with patch.dict(sys.modules, {"config": SimpleNamespace(is_configured=lambda: True)}):
            frames = [copy.deepcopy(frame) async for frame in self.ns["send_task"]("question", [], [])]
        self.assertEqual(frames[0][-1], "")
        self.assertEqual(frames[0][0][-1]["metadata"]["status"], "pending")
        self.assertTrue(all(frame[-1] == {"__type__": "update"} for frame in frames[1:]))

    async def test_blocked_or_failed_switch_preserves_approval_and_messages(self):
        for busy in (True, False):
            self.service.is_busy.return_value = busy
            result = await self.ns["switch_conversation_ui"]("other")
            for index in (0, 4, 5, 6, 11, 12):
                self.assertEqual(result[index], {"__type__": "update"})
            self.assertEqual(result[8], "original")


if __name__ == "__main__":
    unittest.main()
