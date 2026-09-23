"""运行时单例与共享常量。

gui.py、gui_handlers.py 都要用到同一批路径常量和同一个 AgentService
实例。单独放在这里，避免两个模块互相 import 形成环。
"""

from pathlib import Path

from agent_service import AgentService

# ============================================================
# 基础路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
AUDIT_FILE = PROJECT_ROOT / "data" / "tool_audit.jsonl"

WORKSPACE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# Agent Service
#
# 全局单例。同一进程内只有一个 AgentService，
# 因此同一时刻只服务一个会话；多标签页会互相打断。
# 这是当前架构的已知限制。
# ============================================================

service = AgentService()

# ============================================================
# 中文名称映射
# ============================================================

TOOL_NAMES = {
    "list_files": "列出文件",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "calculator": "计算器",
    "get_current_time": "获取当前时间",
    "save_note": "保存笔记",
    "read_notes": "读取笔记",
    "finish_task": "结束任务",
    "web_search": "联网搜索",
    "web_fetch": "读取网页",
    "github_trending": "GitHub 热门",
}

TRACE_HEADERS = [
    "时间",
    "状态",
    "工具",
    "详情",
]

AUDIT_HEADERS = [
    "时间",
    "事件",
    "工具",
    "任务",
    "详情",
]
