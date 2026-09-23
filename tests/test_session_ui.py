"""会话列表与 GUI 模块边界。

背景：把 9900 行的 gui.py 拆开时，SEND_OUTPUTS 和 SESSION_SWITCH_OUTPUTS
这两个「组件输出组」留在了 gui.py 的 Blocks 里，而用到它们的
send_task / switch_conversation_ui 搬到了 gui_handlers.py。
结果就是 NameError —— 而且只藏在「任务忙」「切换失败」这些分支里，
平时点不出来。这里把这类越界引用钉死。
"""

import ast
import builtins
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def module_definitions(filename):
    """模块里真正定义的名字（不含 import 进来的）。"""
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    names = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if hasattr(node, "args"):
                for arg in node.args.args + node.args.kwonlyargs:
                    names.add(arg.arg)

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name):
                        names.add(child.id)

        elif isinstance(node, (ast.For, ast.comprehension)):
            target = getattr(node, "target", None)
            if target is not None:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name):
                        names.add(child.id)

        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)

    return names


def module_loaded_names(filename):
    """模块里读取过的全局名字。"""
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    loaded = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)

    return loaded


class ModuleBoundaryTests(unittest.TestCase):
    def test_handlers_do_not_reach_into_gui_layout(self):
        """gui_handlers 不能引用 gui.py 里定义的常量。

        gui.py 不能反向 import gui.py（会成环），
        所以任何这类引用都是运行期 NameError。
        """
        gui_defined = module_definitions("gui.py")
        handlers_defined = module_definitions("gui_handlers.py")
        handlers_loaded = module_loaded_names("gui_handlers.py")

        offenders = sorted(
            (handlers_loaded & gui_defined)
            - handlers_defined
            - set(dir(builtins))
        )

        self.assertEqual(
            offenders,
            [],
            "gui_handlers.py 引用了只在 gui.py 里定义的名字，"
            f"运行到对应分支会 NameError：{offenders}",
        )

    def test_output_group_lengths_are_declared(self):
        """skip 元组的长度常量必须存在且为正。"""
        import gui_handlers

        self.assertGreater(gui_handlers.SEND_OUTPUT_COUNT, 0)
        self.assertGreater(gui_handlers.SESSION_SWITCH_OUTPUT_COUNT, 0)

        self.assertEqual(
            len(
                gui_handlers.skip_tuple(
                    gui_handlers.SEND_OUTPUT_COUNT
                )
            ),
            gui_handlers.SEND_OUTPUT_COUNT,
        )


class ConversationChoiceTests(unittest.TestCase):
    """会话下拉的标签必须能区分不同会话。"""

    def choices_for(self, sessions, current="s1"):
        import gui_handlers

        fake = SimpleNamespace(
            list_conversations=lambda: sessions,
            get_session_id=lambda: current,
        )

        with patch.object(gui_handlers, "service", fake):
            return gui_handlers.conversation_choices()

    def test_labels_are_distinguishable(self):
        sessions = [
            {
                "session_id": "s1",
                "title": "",
                "item_count": 0,
                "updated_at": "2026-09-23 09:00:00",
            },
            {
                "session_id": "s2",
                "title": "",
                "item_count": 0,
                "updated_at": "2026-09-23 10:00:00",
            },
            {
                "session_id": "s3",
                "title": "",
                "item_count": 0,
                "updated_at": "2026-09-23 11:00:00",
            },
        ]

        choices = self.choices_for(sessions, current="s1")

        labels = [label for label, _ in choices]

        self.assertEqual(
            len(set(labels)),
            3,
            f"三个会话的标签撞在一起了，用户分不清：{labels}",
        )

    def test_identical_metadata_stays_distinguishable(self):
        """同一秒内新建几个会话，元数据完全一样也必须能分辨。

        这是真实场景：连点几次「新任务」，标题、时间、条数全都相同。
        """
        sessions = [
            {
                "session_id": "v2_4900050b-601a-41c2",
                "title": "",
                "item_count": 0,
                "updated_at": "2026-09-23 10:04:32",
            },
            {
                "session_id": "v2_0a6f56a1-1b58-4684",
                "title": "",
                "item_count": 0,
                "updated_at": "2026-09-23 10:04:32",
            },
        ]

        labels = [
            label
            for label, _ in self.choices_for(
                sessions, current="v2_4900050b-601a-41c2"
            )
        ]

        self.assertEqual(
            len(set(labels)),
            len(labels),
            f"元数据完全相同的会话无法区分：{labels}",
        )

    def test_label_shows_time_and_message_count(self):
        sessions = [
            {
                "session_id": "s1",
                "title": "项目周报",
                "item_count": 12,
                "updated_at": "2026-09-23 17:30:00",
            },
        ]

        label, value = self.choices_for(
            sessions, current="s1"
        )[0]

        self.assertEqual(value, "s1")
        self.assertIn("项目周报", label)
        self.assertIn("09-23 17:30", label)
        self.assertIn("12 条", label)

    def test_current_session_is_marked(self):
        sessions = [
            {
                "session_id": "s1",
                "title": "A",
                "item_count": 1,
                "updated_at": "2026-09-23 17:30:00",
            },
            {
                "session_id": "s2",
                "title": "B",
                "item_count": 1,
                "updated_at": "2026-09-23 18:30:00",
            },
        ]

        labels = {
            value: label
            for label, value in self.choices_for(
                sessions, current="s2"
            )
        }

        self.assertTrue(labels["s2"].startswith("●"))
        self.assertFalse(labels["s1"].startswith("●"))


if __name__ == "__main__":
    unittest.main()
