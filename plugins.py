"""Single-file plugin system for 小智.

一个插件就是一个 .py 文件，放在 `plugins/` 目录下：

    PLUGIN_NAME = "示例插件"          # 显示名（缺省用文件名）
    PLUGIN_VERSION = "0.1.0"
    PLUGIN_AUTHOR = "your-name"
    PLUGIN_DESC = "一句话说明这个插件做什么"
    PLUGIN_INSTRUCTIONS = "追加给模型的补充约束（可选）"

    from agents import function_tool

    @function_tool
    def my_tool(text: str) -> str:
        '''工具 docstring 会作为说明展示给模型。'''
        return text.upper()

    PLUGIN_TOOLS = [my_tool]

也支持目录形态：`plugins/<name>/plugin.py`。

安全说明（务必知悉）：
插件是**本机 Python 代码**，启用即等同于运行它，
拥有与小智相同的权限——能读文件、能起进程、能联网。
只安装你信得过的插件，安装后默认是"未启用"状态。
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import uuid
from copy import deepcopy
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PLUGIN_DIR = BASE_DIR / "plugins"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

STATE_PATH = DATA_DIR / "plugins.json"

MAX_PLUGIN_BYTES = 512 * 1024

DEFAULT_STATE = {
    "version": 1,
    "enabled": {},
}

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


# ============================================================
# 状态文件
# ============================================================

def _read_state() -> dict:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return deepcopy(DEFAULT_STATE)
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_STATE)
    state = deepcopy(DEFAULT_STATE)
    state.update(raw)
    if not isinstance(state.get("enabled"), dict):
        state["enabled"] = {}
    return state


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_enabled(plugin_id: str) -> bool:
    return bool(_read_state()["enabled"].get(plugin_id, False))


def _set_enabled_flag(plugin_id: str, enabled: bool) -> None:
    state = _read_state()
    state["enabled"][plugin_id] = bool(enabled)
    _write_state(state)


def _drop_state(plugin_id: str) -> None:
    state = _read_state()
    state["enabled"].pop(plugin_id, None)
    _write_state(state)


# ============================================================
# 发现与加载
# ============================================================

def safe_plugin_id(raw: str) -> str:
    """把任意文件名压成安全的插件 id。"""
    cleaned = _SAFE_ID.sub("_", Path(str(raw)).stem).strip("_")
    return cleaned or "plugin"


def _iter_plugin_files():
    """产出 (plugin_id, 模块入口路径, 展示目录)。"""
    if not PLUGIN_DIR.exists():
        return

    for entry in sorted(PLUGIN_DIR.iterdir(), key=lambda p: p.name.lower()):
        # 目录插件：plugins/<name>/plugin.py
        if entry.is_dir():
            module = entry / "plugin.py"
            if module.is_file():
                yield safe_plugin_id(entry.name), module, entry
            continue

        if entry.suffix.lower() != ".py":
            continue
        if entry.name.startswith("_"):
            continue
        yield safe_plugin_id(entry.name), entry, entry.parent


def _load_module(path: Path, plugin_id: str):
    """用一次性模块名加载插件，不写进 sys.modules。

    不缓存是刻意的：改文件后重新启用能立刻拿到新代码。
    """
    module_name = f"xiaozhi_plugin_{plugin_id}_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件文件：{path.name}")

    module = importlib.util.module_from_spec(spec)
    # 少数插件会用到 __main__ 之外的相对导入兜底，这里把目录放进搜索路径。
    parent = str(path.parent)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    try:
        spec.loader.exec_module(module)
    finally:
        if added:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass
    return module


def _tool_names(module) -> list[str]:
    tools = getattr(module, "PLUGIN_TOOLS", None)
    if not isinstance(tools, (list, tuple)):
        return []
    names = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name:
            names.append(str(name))
    return names


def _collect_tools(module) -> list:
    tools = getattr(module, "PLUGIN_TOOLS", None)
    if not isinstance(tools, (list, tuple)):
        return []
    return [tool for tool in tools if tool is not None]


def inspect_plugin(plugin_id: str, path: Path) -> dict:
    """读取插件元信息。加载失败的插件也要能显示出来。"""
    info = {
        "id": plugin_id,
        "file": str(path.relative_to(BASE_DIR)).replace("\\", "/"),
        "name": path.stem if path.name == "plugin.py" else path.stem,
        "desc": "",
        "version": "—",
        "author": "—",
        "tools": [],
        "instructions": False,
        "enabled": is_enabled(plugin_id),
        "error": None,
        "size": 0,
    }

    try:
        info["size"] = path.stat().st_size
    except OSError:
        pass

    try:
        module = _load_module(path, plugin_id)
    except BaseException as error:  # noqa: BLE001 - 插件代码任意异常都要兜住
        info["error"] = f"{type(error).__name__}: {error}"
        return info

    info["name"] = str(getattr(module, "PLUGIN_NAME", "") or info["name"])
    info["desc"] = str(getattr(module, "PLUGIN_DESC", "") or "")
    info["version"] = str(getattr(module, "PLUGIN_VERSION", "") or "—")
    info["author"] = str(getattr(module, "PLUGIN_AUTHOR", "") or "—")
    info["tools"] = _tool_names(module)
    info["instructions"] = bool(str(getattr(module, "PLUGIN_INSTRUCTIONS", "") or ""))
    return info


def list_plugins() -> list[dict]:
    rows = [
        inspect_plugin(plugin_id, path)
        for plugin_id, path, _ in _iter_plugin_files()
    ]
    rows.sort(key=lambda row: (not row["enabled"], row["name"].lower()))
    return rows


def get_plugin(plugin_id: str) -> dict | None:
    for found_id, path, _ in _iter_plugin_files():
        if found_id == plugin_id:
            return inspect_plugin(found_id, path)
    return None


def _plugin_path(plugin_id: str) -> Path | None:
    for found_id, path, _ in _iter_plugin_files():
        if found_id == plugin_id:
            return path
    return None


# ============================================================
# 安装 / 卸载 / 启停
# ============================================================

def install_plugin(source: str) -> tuple[bool, str, str | None]:
    """把一个 .py 文件复制进 plugins/ 并校验能否加载。

    返回 (是否成功, 提示文案, plugin_id)。
    安装后默认不启用，需要用户显式开启。
    """
    if not source:
        return False, "请先选择一个 .py 插件文件。", None

    src = Path(str(source)).resolve()

    if not src.is_file():
        return False, "插件文件不存在，请重新选择。", None
    if src.suffix.lower() != ".py":
        return False, "插件必须是 .py 文件。", None
    try:
        if src.stat().st_size > MAX_PLUGIN_BYTES:
            return False, f"插件文件超过 {MAX_PLUGIN_BYTES // 1024} KB，已拒绝安装。", None
    except OSError as error:
        return False, f"无法读取插件文件：{error}", None

    stem = safe_plugin_id(src.name)
    target = PLUGIN_DIR / f"{stem}.py"

    index = 1
    while target.exists():
        target = PLUGIN_DIR / f"{stem}_{index}.py"
        index += 1

    try:
        shutil.copy2(src, target)
    except OSError as error:
        return False, f"复制插件失败：{error}", None

    plugin_id = safe_plugin_id(target.name)

    try:
        info = inspect_plugin(plugin_id, target)
    except BaseException as error:  # noqa: BLE001
        try:
            target.unlink()
        except OSError:
            pass
        return False, f"插件加载失败，已撤销安装：{type(error).__name__}: {error}", None

    if info["error"]:
        try:
            target.unlink()
        except OSError:
            pass
        return False, f"插件导入报错，已撤销安装：{info['error']}", None

    if not info["tools"] and not info["instructions"]:
        try:
            target.unlink()
        except OSError:
            pass
        return (
            False,
            "这不是有效插件：文件里既没有 PLUGIN_TOOLS，"
            "也没有 PLUGIN_INSTRUCTIONS，已撤销安装。",
            None,
        )

    _set_enabled_flag(plugin_id, False)
    _sync_agent()
    return (
        True,
        f"已安装「{info['name']}」（{len(info['tools'])} 个工具）。"
        "默认未启用：确认来源可信后点「启用」即可生效，无需重启。",
        plugin_id,
    )


def uninstall_plugin(plugin_id: str) -> tuple[bool, str]:
    path = _plugin_path(plugin_id)
    if path is None:
        return False, "插件不存在，可能已被删除。"

    try:
        if path.parent != PLUGIN_DIR and path.name == "plugin.py":
            shutil.rmtree(path.parent, ignore_errors=True)
        else:
            path.unlink()
    except OSError as error:
        return False, f"删除插件失败：{error}"

    _drop_state(plugin_id)
    _sync_agent()
    return True, f"已卸载插件「{plugin_id}」。"


def set_plugin_enabled(plugin_id: str, enabled: bool) -> tuple[bool, str]:
    info = get_plugin(plugin_id)
    if info is None:
        return False, "插件不存在，可能已被删除。"

    if info["error"]:
        return False, f"插件无法启用，导入时报错：{info['error']}"

    _set_enabled_flag(plugin_id, enabled)
    applied = _sync_agent()

    verb = "启用" if enabled else "停用"
    if not enabled:
        return True, f"已{verb}「{info['name']}」，其工具已从当前会话移除。"

    skipped = [name for name in info["tools"] if name in applied["skipped"]]
    tail = ""
    if skipped:
        tail = f"；工具 {', '.join(skipped)} 与内置工具重名，已跳过。"
    return True, f"已{verb}「{info['name']}」，{len(applied['added'])} 个工具已生效，无需重启。{tail}"


# ============================================================
# 注入到 Agent
# ============================================================

def load_plugin_tools() -> tuple[list, list[str]]:
    """返回 (启用插件的工具列表, 因重名被跳过的工具名)。"""
    tools: list = []
    seen: set[str] = set()

    for plugin_id, path, _ in _iter_plugin_files():
        if not is_enabled(plugin_id):
            continue
        try:
            module = _load_module(path, plugin_id)
        except BaseException:  # noqa: BLE001 - 坏插件不能拖垮启动
            continue
        for tool in _collect_tools(module):
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if not name or name in seen:
                continue
            seen.add(str(name))
            tools.append(tool)

    return tools, []


def plugin_instructions() -> str:
    blocks = []
    for plugin_id, path, _ in _iter_plugin_files():
        if not is_enabled(plugin_id):
            continue
        try:
            module = _load_module(path, plugin_id)
        except BaseException:  # noqa: BLE001
            continue
        text = str(getattr(module, "PLUGIN_INSTRUCTIONS", "") or "").strip()
        if text:
            blocks.append(f"【插件 {plugin_id}】\n{text}")
    return "\n\n".join(blocks)


def _agent_ref():
    try:
        from app_agents.personal_agent import personal_agent
    except Exception:  # noqa: BLE001 - 插件系统不能阻断其它调用方
        return None
    return personal_agent


def _sync_agent() -> dict:
    """把启用中的插件工具写回 Agent。

    第一次调用时把原始工具/提示词快照到 agent 上，
    之后每次都基于快照重建，避免反复叠加。
    """
    result = {"added": [], "skipped": []}
    agent = _agent_ref()
    if agent is None:
        return result

    base_tools = getattr(agent, "_xiaozhi_base_tools", None)
    if base_tools is None:
        base_tools = list(getattr(agent, "tools", []) or [])
        agent._xiaozhi_base_tools = base_tools

    base_instructions = getattr(agent, "_xiaozhi_base_instructions", None)
    if base_instructions is None:
        base_instructions = getattr(agent, "instructions", "") or ""
        agent._xiaozhi_base_instructions = base_instructions

    existing = {
        getattr(tool, "name", None) or getattr(tool, "__name__", None)
        for tool in base_tools
    }

    tools, _ = load_plugin_tools()
    kept = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name in existing:
            result["skipped"].append(str(name))
            continue
        kept.append(tool)
        result["added"].append(str(name))
        existing.add(name)

    try:
        agent.tools = list(base_tools) + kept
    except Exception:  # noqa: BLE001
        pass

    extra = plugin_instructions()
    try:
        if extra:
            agent.instructions = (
                f"{base_instructions}\n\n"
                "========================\n"
                "插件补充规则\n"
                "========================\n"
                f"{extra}"
            )
        else:
            agent.instructions = base_instructions
    except Exception:  # noqa: BLE001
        pass

    return result


def sync_agent_tools() -> dict:
    """供启动时调用：加载已启用插件。"""
    return _sync_agent()


# ============================================================
# 展示
# ============================================================

def plugin_choices() -> list[tuple[str, str]]:
    rows = []
    for info in list_plugins():
        flag = "●" if info["enabled"] else "○"
        label = f"{flag} {info['name']}（{info['id']}）"
        rows.append((label, info["id"]))
    return rows


def plugin_summary() -> tuple[int, int]:
    rows = list_plugins()
    enabled = sum(1 for row in rows if row["enabled"])
    return enabled, len(rows)


def plugin_rows() -> list[list[str]]:
    rows = []
    for info in list_plugins():
        state = "已启用" if info["enabled"] else "未启用"
        if info["error"]:
            state = "加载失败"
        tools = "、".join(info["tools"][:4]) or "—"
        if len(info["tools"]) > 4:
            tools += f" 等 {len(info['tools'])} 个"
        rows.append([
            info["name"],
            info["version"],
            state,
            tools,
            info["desc"] or "—",
        ])
    return rows


PLUGIN_TABLE_HEADERS = ["插件", "版本", "状态", "工具", "说明"]


def plugin_detail_markdown(plugin_id: str | None) -> str:
    if not plugin_id:
        return "在上方选择一个插件查看详情。"

    info = get_plugin(plugin_id)
    if info is None:
        return "插件不存在，可能已被删除。"

    state = "已启用" if info["enabled"] else "未启用"
    lines = [
        f"### {info['name']}",
        "",
        f"- 标识：`{info['id']}`",
        f"- 版本：{info['version']} · 作者：{info['author']}",
        f"- 状态：**{state}**",
        f"- 文件：`{info['file']}`",
    ]

    if info["tools"]:
        lines.append("- 工具：" + "、".join(f"`{name}`" for name in info["tools"]))
    else:
        lines.append("- 工具：无（只提供提示词补充）")

    if info["instructions"]:
        lines.append("- 提示词补充：有")

    if info["desc"]:
        lines.extend(["", info["desc"]])

    if info["error"]:
        lines.extend(["", f"⚠️ 加载失败：`{info['error']}`"])

    return "\n".join(lines)


def plugin_template() -> str:
    return '''"""小智插件模板。

改名后放进 plugins/ 目录即可被识别。
"""
from agents import function_tool


PLUGIN_NAME = "我的插件"
PLUGIN_VERSION = "0.1.0"
PLUGIN_AUTHOR = "your-name"
PLUGIN_DESC = "一句话说明这个插件做什么"
PLUGIN_INSTRUCTIONS = "（可选）追加给模型的补充规则"


@function_tool
def shout(text: str) -> str:
    """把输入文本转成大写。

    docstring 会作为工具说明展示给模型，请写清楚。
    """
    return text.upper()



PLUGIN_TOOLS = [shout]
'''
