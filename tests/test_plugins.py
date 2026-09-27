"""插件系统安全边界的回归测试。

历史故障：list_plugins / get_plugin 为了读几个常量
把插件模块 exec 一遍 —— 把恶意 .py 丢进 plugins/ 再
刷新插件页就能触发执行，且不需要用户点「启用」。
现在浏览路径必须只做 AST 静态读取。
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import plugins
from plugins import _static_metadata, inspect_plugin


VALID_PLUGIN = '''
PLUGIN_NAME = "示例插件"
PLUGIN_VERSION = "1.2.0"
PLUGIN_AUTHOR = "someone"
PLUGIN_DESC = "做一件事"
PLUGIN_INSTRUCTIONS = "补充规则"

from agents import function_tool

@function_tool
def shout(text: str) -> str:
    return text.upper()

@function_tool
def whisper(text: str) -> str:
    return text.lower()

PLUGIN_TOOLS = [shout, whisper]
'''

# 顶层有真实副作用 + 顶层即崩坏的插件：
# 浏览它时两件事都必须成立 —— 副作用没发生、崩溃没被触发。
SIDE_EFFECT_PLUGIN = '''
import sys
with open(sys.argv[1] if False else MARKER_PATH, "w", encoding="utf-8") as f:
    f.write("executed")
raise RuntimeError("浏览我不该运行我")
PLUGIN_NAME = "不该被看到"
'''


class TempPluginCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)  # 临时目录留给系统清理
        # inspect_plugin 的 file 字段相对 BASE_DIR 计算
        patcher = patch.object(plugins, "BASE_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _plugin_file(self, content: str, name="plugin_under_test.py") -> Path:
        path = self.tmp / name
        path.write_text(content, encoding="utf-8")
        return path


class StaticMetadataTests(TempPluginCase):
    def test_reads_constants_without_exec(self):
        path = self._plugin_file(VALID_PLUGIN)
        meta = _static_metadata(path)

        self.assertEqual("示例插件", meta["name"])
        self.assertEqual("1.2.0", meta["version"])
        self.assertEqual("someone", meta["author"])
        self.assertEqual("做一件事", meta["desc"])
        self.assertEqual("补充规则", meta["instructions"])
        self.assertEqual(["shout", "whisper"], meta["tools"])
        self.assertIsNone(meta["error"])

    def test_module_top_level_never_runs(self):
        """回归核心：顶层写文件 / raise 的插件，静态读取时不会执行。"""
        marker = self.tmp / "boom.txt"
        path = self._plugin_file(
            SIDE_EFFECT_PLUGIN.replace("MARKER_PATH", repr(str(marker)))
        )

        meta = _static_metadata(path)

        self.assertFalse(marker.exists(), "插件代码在浏览时被执行了！")
        self.assertIsNone(meta["error"])
        # 静态读取仍然拿到了 raise 之后的常量
        self.assertEqual("不该被看到", meta["name"])

    def test_falls_back_to_decorated_functions(self):
        path = self._plugin_file(
            "from agents import function_tool\n"
            "@function_tool\n"
            "def solo() -> str:\n"
            "    return 'x'\n"
        )
        meta = _static_metadata(path)
        self.assertEqual(["solo"], meta["tools"])

    def test_syntax_error_is_reported_not_raised(self):
        path = self._plugin_file("def broken(:\n")
        meta = _static_metadata(path)
        self.assertIsNotNone(meta["error"])


class InspectPluginTests(TempPluginCase):
    def test_list_path_uses_static_only(self):
        """list_plugins 走的默认路径绝不允许 exec 模块。"""
        marker = self.tmp / "boom.txt"
        path = self._plugin_file(
            SIDE_EFFECT_PLUGIN.replace("MARKER_PATH", repr(str(marker)))
        )
        executed = []

        real_load = plugins._load_module

        def spy(module_path, plugin_id):
            executed.append(module_path)
            return real_load(module_path, plugin_id)

        with patch("plugins._load_module", side_effect=spy):
            info = inspect_plugin("demo", path)

        self.assertEqual([], executed)
        self.assertFalse(marker.exists(), "插件代码在浏览时被执行了！")
        self.assertEqual("不该被看到", info["name"])

    def test_install_validation_still_loads(self):
        """安装校验是显式动作，仍应真正加载并读取真实元数据。"""
        path = self._plugin_file(VALID_PLUGIN)
        executed = []

        real_load = plugins._load_module

        def spy(module_path, plugin_id):
            executed.append(module_path)
            return real_load(module_path, plugin_id)

        with patch("plugins._load_module", side_effect=spy), patch(
            "plugins.is_enabled", return_value=False
        ):
            info = inspect_plugin("demo", path, load=True)

        self.assertEqual(1, len(executed))
        self.assertIsNone(info["error"])
        self.assertEqual(["shout", "whisper"], info["tools"])


if __name__ == "__main__":
    unittest.main()
