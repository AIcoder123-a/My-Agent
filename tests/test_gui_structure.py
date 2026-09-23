"""守护 GUI 的模块边界。

gui.py 曾经有 9900 行：1800 行 CSS 字面量、900 行 JS、4700 行事件处理
函数和一个 2200 行的 Blocks 布局全挤在一个文件里。拆开之后，这里守住
边界，避免下次改动又把样式搬回去。
"""

import ast
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def top_level_assign_sizes(filename):
    """返回 (名字, 行数) 列表，只看模块级字符串赋值。"""
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        span = (node.end_lineno or node.lineno) - node.lineno + 1
        name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else "?"
        out.append((name, span))
    return out


class GuiStructureTests(unittest.TestCase):
    def test_gui_stays_small(self):
        """gui.py 只负责组装界面，不应该再长回去。"""
        lines = len((ROOT / "gui.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(
            lines,
            3000,
            f"gui.py 已经 {lines} 行；大段样式/脚本/处理函数应放进各自模块。",
        )

    def test_no_bulk_literals_in_gui(self):
        """gui.py 里不允许再出现几百行的字符串字面量。"""
        for name, span in top_level_assign_sizes("gui.py"):
            self.assertLess(
                span,
                120,
                f"gui.py 的 {name} 有 {span} 行，应该移到 gui_assets.py。",
            )

    def test_assets_module_owns_the_bulk(self):
        """大块样式与脚本确实住在 gui_assets.py / assets/。"""
        self.assertTrue((ROOT / "assets" / "app.css").exists())
        bulk = [span for _, span in top_level_assign_sizes("gui_assets.py")]
        self.assertTrue(
            any(span > 100 for span in bulk),
            "gui_assets.py 里没找到大块资产，拆分可能没有真正生效。",
        )

    def test_split_modules_are_importable(self):
        """拆分出来的模块都能独立导入，没有循环依赖。"""
        for module in (
            "app_runtime",
            "gui_theme",
            "gui_assets",
            "gui_handlers",
        ):
            with self.subTest(module=module):
                __import__(module)

    def test_service_is_a_single_shared_instance(self):
        """gui 与 handlers 必须共用同一个 AgentService，不能各建一个。"""
        import app_runtime
        import gui_handlers

        self.assertIs(gui_handlers.service, app_runtime.service)

    def test_assets_expose_what_gui_needs(self):
        import gui_assets

        for name in ("CSS", "KEYBOARD_JS", "COMPOSER_MENU_HTML"):
            with self.subTest(name=name):
                self.assertTrue(
                    isinstance(getattr(gui_assets, name), str)
                    and getattr(gui_assets, name),
                )


if __name__ == "__main__":
    unittest.main()
