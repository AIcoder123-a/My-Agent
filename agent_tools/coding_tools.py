"""Bounded workspace operations and explicitly approved local terminal execution."""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event, Lock

from agents.decorators import tool
from agent_tools.file_tools import safe_workspace_path
from tool_errors import tool_error_to_model

_active: dict[int, Event] = {}
_active_lock = Lock()
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv'}


def cancel_active_shells():
    with _active_lock:
        for event in _active.values():
            event.set()


def search_workspace_impl(query: str, path: str = '.', max_results: int = 50) -> str:
    if not query.strip():
        raise ValueError('搜索内容不能为空。')
    root = safe_workspace_path(path)
    if not root.is_dir():
        raise ValueError('搜索路径必须是目录。')
    limit = max(1, min(max_results, 100))
    matches, scanned, truncated = [], 0, False
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not Path(parent, d).is_symlink()]
        for name in names:
            candidate = Path(parent, name)
            scanned += 1
            if scanned > 2000:
                truncated = True
                break
            try:
                candidate = safe_workspace_path(str(candidate))
                if candidate.stat().st_size > 2 * 1024 * 1024:
                    continue
                content = candidate.read_text(encoding='utf-8')
                if '\x00' in content:
                    continue
            except (OSError, UnicodeError, ValueError):
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query.casefold() in line.casefold():
                    from paths import WORKSPACE_DIR
                    matches.append({'path': candidate.relative_to(WORKSPACE_DIR.resolve()).as_posix(),
                                    'line': number, 'text': line[:500]})
                    if len(matches) >= limit:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break
    return json.dumps({'matches': matches, 'truncated': truncated}, ensure_ascii=False)


@tool(failure_error_function=tool_error_to_model)
def search_workspace(query: str, path: str = '.', max_results: int = 50) -> str:
    """在工作区文本文件中搜索字面内容，返回路径、行号；跳过依赖与大文件。"""
    return search_workspace_impl(query, path, max_results)


@tool(failure_error_function=tool_error_to_model)
def read_file_lines(path: str, start_line: int = 1, count: int = 120) -> str:
    """读取 UTF-8 文件指定行段，带行号。用于代码定位，最多读取 300 行。"""
    target = safe_workspace_path(path)
    if target.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('文本超过 5 MB，请使用终端按需处理。')
    if start_line < 1 or count < 1:
        raise ValueError('起始行和行数必须为正数。')
    lines = target.read_text(encoding='utf-8').splitlines()
    return '\n'.join(f'{i + 1}: {lines[i][:2000]}'
                     for i in range(start_line - 1, min(len(lines), start_line - 1 + min(count, 300))))


def edit_file_impl(path: str, old_text: str, new_text: str) -> str:
    target = safe_workspace_path(path)
    if not old_text:
        raise ValueError('待替换内容不能为空。')
    if target.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('精确编辑仅支持 5 MB 以内的文本。')
    original_bytes = target.read_bytes()
    original = original_bytes.decode('utf-8')
    # Models usually provide LF strings even when Windows files use CRLF.
    newline = '\r\n' if '\r\n' in original else '\n'
    old_text = old_text.replace('\r\n', '\n').replace('\n', newline)
    new_text = new_text.replace('\r\n', '\n').replace('\n', newline)
    if original.count(old_text) != 1:
        raise ValueError('待替换内容必须恰好匹配一次，请重新读取并提供唯一上下文。')
    updated = original.replace(old_text, new_text, 1)
    handle, temp = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(updated.encode('utf-8'))
        if target.read_bytes() != original_bytes:
            raise ValueError('文件已被其他操作修改，请重新读取后编辑。')
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return f'已精确替换：{path}（1 处）'


@tool(needs_approval=True, failure_error_function=tool_error_to_model)
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """经用户批准后精确替换工作区文件中唯一匹配的文本；不匹配则不修改。"""
    return edit_file_impl(path, old_text, new_text)


async def _terminate_tree(process):
    if process.returncode is not None:
        return
    if os.name == 'nt':
        killer = await asyncio.create_subprocess_exec(
            str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'taskkill.exe'),
            '/PID', str(process.pid), '/T', '/F',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        await killer.wait()
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def run_shell_impl(command: str, cwd: str = '.', timeout_seconds: int = 60) -> str:
    if not command.strip() or len(command) > 16000:
        raise ValueError('命令不能为空且不能超过 16000 字符。')
    directory = safe_workspace_path(cwd)
    if not directory.is_dir():
        raise ValueError('工作目录不存在。')
    timeout = max(1, min(int(timeout_seconds), 120))
    # Do not pass model/MCP credentials through inherited environment variables.
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL'))}
    env['PYTHONIOENCODING'] = 'utf-8'
    if os.name == 'nt':
        executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                         'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
        args = [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new();\n' + command]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW}
    else:
        args, options = ['/bin/sh', '-c', command], {'start_new_session': True}
    stopped = Event()
    process = None
    reason = 'completed'
    with tempfile.TemporaryFile() as output:
        try:
            process = await asyncio.create_subprocess_exec(
                *args, cwd=str(directory), env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=output, stderr=asyncio.subprocess.STDOUT, **options)
            with _active_lock:
                _active[process.pid] = stopped
            deadline = time.monotonic() + timeout
            while process.returncode is None:
                if stopped.is_set() or time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > 2 * 1024 * 1024:
                    reason = 'cancelled' if stopped.is_set() else ('timeout' if time.monotonic() >= deadline else 'output_limit')
                    await _terminate_tree(process)
                    break
                await asyncio.sleep(0.1)
            await process.wait()
            output.seek(0)
            data = output.read(32001)
            return json.dumps({'status': reason, 'exit_code': process.returncode,
                               'output': data[:32000].decode('utf-8', errors='replace'),
                               'truncated': len(data) > 32000}, ensure_ascii=False)
        finally:
            if process:
                await _terminate_tree(process)
                with _active_lock:
                    _active.pop(process.pid, None)


@tool(needs_approval=True, failure_error_function=tool_error_to_model)
async def run_shell(command: str, cwd: str = '.', timeout_seconds: int = 60) -> str:
    """审批后执行本地 PowerShell 命令，用于代码、测试、Git、数据处理。工作目录位于 workspace，最长 120 秒。终端具有当前系统用户权限，并非文件系统沙箱，必须先说明命令意图；不能通过终端绕过被拒绝的操作。"""
    return await run_shell_impl(command, cwd, timeout_seconds)
