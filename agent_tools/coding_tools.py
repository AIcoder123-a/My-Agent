"""Bounded workspace operations and explicitly approved local terminal execution."""
import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event, Lock

from agents.decorators import tool
from agent_tools.file_tools import safe_workspace_path
from tool_errors import tool_error_to_model

import workspace_snapshots

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
    # 替换前留一份旧内容，用户改坏了可以撤销。
    workspace_snapshots.capture(path, action='edit_file')
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


# ============================================================
# 终端命令静态审计
#
# 原先的问题：
#   run_shell 只校验了 cwd 落在 workspace 内，
#   命令本身却以当前 Windows 用户的完整权限执行。
#   一条 `Get-Content D:\myagent-clean\.env` 就能把 API Key 读走，
#   所谓「工作区边界」对终端形同虚设；
#   靠 prompt 里写一句「不得越权」是把安全交给模型自觉，
#   这在产品里不成立。
#
# 这里补一层静态审计。设计原则：
#   - 只拦高置信度的危险模式，避免误伤正常开发命令；
#   - 读取类命令允许绝对路径（否则连 python.exe 都调不动），
#     但凭据类文件名一律拒绝；
#   - 写入 / 删除 / 移动类命令，目标绝对路径必须落在工作区或临时目录内。
#
# 说明：这不是完整沙箱（真沙箱需要容器 / 降权进程），
# 但能把「随手一条命令把密钥读走」「rm -rf 掉工作区外的目录」
# 这两类最高频事故挡住。
# ============================================================

# 凭据 / 密钥文件
_SENSITIVE_FILE_RE = re.compile(
    r"(\.env(\.|$|[\s\"']))"
    r"|(secrets?\.)"
    r"|(credentials?)"
    r"|(id_(rsa|dsa|ecdsa|ed25519))"
    r"|(\.pem\b)"
    r"|(\.pfx\b)"
    r"|(\.key\b)"
    r"|(\.npmrc\b)"
    r"|(\.git-credentials)"
    r"|(\.htpasswd)"
    r"|(\.netrc\b)"
    r"|((^|[/\\])\.kube[/\\]config\b)"
    r"|((^|[/\\])\.docker[/\\]config\.json\b)"
    r"|((^|[/\\])\.aws[/\\])"
    r"|((^|[/\\])\.ssh[/\\])"
    r"|((^|[/\\])\.gnupg[/\\])"
    r"|(service[-_]?account)"
    r"|(token\.json\b)"
    r"|(\bkeytab\b)",
    re.IGNORECASE,
)

# 系统级破坏性 / 高危操作
_DANGEROUS_RE = re.compile(
    r"(\bformat-volume\b)"
    r"|(\bdiskpart\b)"
    r"|(\bbcdedit\b)"
    r"|(\breg\s+(delete|add|import)\b)"
    r"|(\bnet\s+(user|localgroup|share|accounts)\b)"
    r"|(\bschtasks\b)"
    r"|(\bsc(\.exe)?\s+(create|delete|config|stop|start|failure)\b)"
    r"|(\bset-executionpolicy\b)"
    r"|(\b(stop|restart)-computer\b)"
    r"|(\bshutdown(\.exe)?\b)"
    r"|(\btaskkill\b)"
    r"|(del\s+/[fqs])"
    r"|(\brm\s+-rf\s+[/\\])"
    r"|(\bformat\s+[a-z]:)"
    r"|(cipher\s+/w)"
    r"|(vssadmin\s+delete)"
    r"|(wmic\s+\S+\s+delete)",
    re.IGNORECASE,
)

# 下载即执行：iwr ... | iex，以及反向管道 (iwr ...) | iex、
# certutil/bitsadmin 这类替代下载通道。
_DOWNLOAD_EXEC_RE = re.compile(
    r"(invoke-expression|iex)\s*[\(\s]+[^)]*"
    r"(invoke-webrequest|iwr|invoke-restmethod|irm|webclient"
    r"|downloadstring|downloadfile)"
    r"|((iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget"
    r"|certutil|bitsadmin|mshta)[^|;&]{0,400}[|;&]\s*"
    r"(iex|invoke-expression)\b)"
    r"|(certutil\s+-urlcache)"
    r"|(bitsadmin\s+/transfer)",
    re.IGNORECASE,
)

# 写入 / 删除 / 移动 / 复制类命令（含 PowerShell 别名）
_WRITE_CMD_RE = re.compile(
    r"\b("
    r"remove-item|rm|del|erase|rd|rmdir|"
    r"new-item|ni|mkdir|md|"
    r"set-content|sc|out-file|add-content|"
    r"move-item|mv|move|ren|rename-item|"
    r"copy-item|cp|copy|xcopy|robocopy|"
    r"clear-content"
    r")\b",
    re.IGNORECASE,
)

# 绝对路径：盘符路径或 UNC 路径
_ABS_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_\\])([A-Za-z]:[\\/][^\s\"'`|;&]*))"
    r"|(?:(\\\\[^\s\"'`|;&]+))"
)

# 写 / 删类命令中的相对路径上跳（`..\`、`../`）。
# cwd 固定在 workspace 内，向上跳一级就必然离开 workspace，
# 因此不做归一化尝试，直接拒绝 —— 这堵住了旧实现只查
# 字面绝对路径、`Set-Content ..\gui.py` 直接写穿的洞。
# 三种形态都拦：句首 / 空白后的 `..`（Remove-Item ..\x）、
# 路径中段的 `\..\`、以及指向父目录本身的裸 `..`。
_RELATIVE_ESCAPE_RE = re.compile(
    r"((?:^|(?<=\s))\.\.(?=[/\\]|\s|$))"
    r"|([/\\]\.\.(?:[/\\]|\s|$))",
    re.IGNORECASE,
)

# 本项目的运行时配置文件：改写 mcp_servers.json 等于
# 配置「下次打开页面自动拉起的任意进程」，必须由界面操作，
# 不允许终端触碰。
_RUNTIME_CONFIG_RE = re.compile(
    r"(mcp_servers\.json\b)"
    r"|((^|[/\\])settings\.json\b)"
    r"|(plugins\.json\b)",
    re.IGNORECASE,
)

# 嵌套编码 / 隐藏窗口负载：base64 整段命令能让所有静态
# 规则失效，宁可整条拒绝（合法开发场景几乎用不到）。
_ENCODED_PAYLOAD_RE = re.compile(
    r"(-enc(odedcommand)?\b)"
    r"|(-windowstyle\s+hidden\b)"
    r"|(-w\s+hidden\b)",
    re.IGNORECASE,
)

# 解释器一行程序里出现破坏性文件操作：
# `python -c "shutil.rmtree(...)"` 的目标路径不在命令文本里，
# 路径边界规则看不穿 —— 命中这些特征就拒绝。
_INTERPRETER_DESTRUCTIVE_RE = re.compile(
    r"(-c\b|-e\b|-m\b).*("
    r"shutil\.rmtree|os\.rmdir|os\.remove|os\.unlink|"
    r"Path\(.+\)\.unlink|WriteAllText|WriteAllBytes|"
    r"DeleteFile|shutil\.move|os\.rename)",
    re.IGNORECASE | re.DOTALL,
)

# 裸重定向写入（`echo x > target`）：效果等同写命令，
# 但动词不在 _WRITE_CMD_RE 里，旧实现完全不设防。
_REDIRECT_RE = re.compile(
    r"(?<![<>])>{1,2}\s*[^\s|&]",
)

# 环境变量中含这些词的，一律不传给子进程
_CREDENTIAL_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
)


def _is_within(
    path: Path,
    root: Path,
) -> bool:

    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _allowed_write_roots() -> list:

    from paths import (
        WORKSPACE_DIR,
    )

    roots = [
        Path(
            WORKSPACE_DIR
        ).resolve()
    ]

    for key in (
        "TEMP",
        "TMP",
    ):

        value = os.environ.get(
            key
        )

        if value:

            try:
                roots.append(
                    Path(
                        value
                    ).resolve()
                )
            except (
                OSError,
                ValueError,
            ):
                pass

    return roots


def audit_shell_command(
    command: str,
) -> None:
    """
    对即将执行的终端命令做静态审计。

    通过则静默返回；违规抛 ValueError，
    由 failure_error_function 转成给模型的软错误，
    模型能看到拒绝原因并调整命令，而不是整单失败。
    """

    text = (
        command or ""
    ).strip()

    if not text:
        return

    hit = _DANGEROUS_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截（系统级危险操作）："
            f"{hit.group(0)!r}。"
            "终端不具备执行破坏性系统操作的权限。"
        )

    hit = _ENCODED_PAYLOAD_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截："
            "禁止编码 / 隐藏窗口的嵌套命令"
            f"（{hit.group(0)!r}）。"
            "编码负载无法被审计，请直接写出明文命令。"
        )

    hit = _INTERPRETER_DESTRUCTIVE_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截："
            "解释器一行程序中包含文件删除 / 写入操作，"
            "其目标路径无法被审计。"
            "请使用受审批和边界保护的内置工具"
            "（write_file / edit_file）完成。"
        )

    hit = _DOWNLOAD_EXEC_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截："
            "禁止「下载并立即执行」。"
            "请先下载到工作区，确认内容后再执行。"
        )

    hit = _SENSITIVE_FILE_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截（疑似读取凭据）："
            f"{hit.group(0)!r}。"
            "终端不允许读取密钥、令牌、证书等凭据文件。"
        )

    # 只对「写 / 删 / 移 / 复制」类命令（以及裸重定向）
    # 做路径越界检查。读取类命令放开绝对路径，
    # 否则连 python.exe 都调不动。

    is_write = bool(
        _WRITE_CMD_RE.search(
            text
        )
    )

    is_redirect = bool(
        _REDIRECT_RE.search(
            text
        )
    )

    if not (
        is_write
        or is_redirect
    ):
        return

    hit = _RELATIVE_ESCAPE_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截："
            "写入/删除/移动类命令的目标不允许包含 `..` 相对路径"
            f"（命中 {hit.group(0)!r}）。"
            "工作目录固定在 workspace 内，向上跳级即越界；"
            "请改用 workspace 内的相对路径或明确的工作区绝对路径。"
        )

    hit = _RUNTIME_CONFIG_RE.search(
        text
    )

    if hit:

        raise ValueError(
            "命令被安全策略拦截："
            f"{hit.group(0)!r} 是本应用的运行时配置，"
            "不允许通过终端改写（可能被用来注入自动执行的程序）。"
            "请提示用户在「设置」或「MCP」页面操作。"
        )

    roots = _allowed_write_roots()

    for match in _ABS_PATH_RE.finditer(
        text
    ):

        raw = (
            match.group(1)
            or match.group(2)
            or ""
        ).strip().strip(
            '"'
        ).strip(
            "'"
        )

        if not raw:
            continue

        try:
            target = Path(
                raw
            ).resolve()
        except (
            OSError,
            ValueError,
        ):
            continue

        if any(
            _is_within(
                target,
                root,
            )
            for root in roots
        ):
            continue

        raise ValueError(
            "命令被安全策略拦截："
            f"写入/删除操作的目标 {raw!r} "
            "超出工作区范围。"
            f"允许写入的位置：{roots[0]}"
        )


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

    # 安全审计必须在解析 cwd 之前完成，
    # 违规命令一律不落到进程上。
    audit_shell_command(command)

    directory = safe_workspace_path(cwd)
    if not directory.is_dir():
        raise ValueError('工作目录不存在。')
    timeout = max(1, min(int(timeout_seconds), 120))
    # Do not pass model/MCP credentials through inherited environment variables.
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in _CREDENTIAL_ENV_MARKERS)}
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
    """审批后执行本地 PowerShell 命令，用于代码、测试、Git、数据处理。工作目录位于 workspace，最长 120 秒。终端具有当前系统用户权限，并非文件系统沙箱，必须先说明命令意图；不能通过终端绕过被拒绝的操作。安全边界：读取凭据类文件（.env、id_rsa、*.pem 等）会被拒绝；删除/写入类命令的目标绝对路径必须在 workspace 或临时目录内，不允许 `..` 相对路径，也不允许改写本应用的运行时配置（mcp_servers.json、settings.json、plugins.json）；编码命令（-EncodedCommand）、解释器一行程序中的文件删除/写入、「下载即执行」与系统级破坏性操作一律被拒绝。读取工作区外的文件是允许的，但不得用于绕过上述限制。"""
    return await run_shell_impl(command, cwd, timeout_seconds)
