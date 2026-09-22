"""UI attachment import and controlled download staging."""
import os
import shutil
import uuid
from pathlib import Path

from agent_tools.file_tools import safe_workspace_path
from paths import BASE_DIR, DATA_DIR, WORKSPACE_DIR

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def import_attachments(files):
    files = files or []
    if len(files) > 10:
        raise ValueError('每次最多导入 10 个文件。')
    sources = [Path(f) for f in files]
    for source in sources:
        if not source.is_file() or source.stat().st_size > MAX_ATTACHMENT_BYTES:
            raise ValueError('每个附件需为不超过 20 MB 的文件。')
    if not sources:
        return []
    folder = safe_workspace_path('uploads/' + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True)
    imported = []
    try:
        for source in sources:
            target = folder / source.name
            if target.exists():
                target = folder / (uuid.uuid4().hex[:6] + '-' + source.name)
            shutil.copyfile(source, target)
            imported.append(target.relative_to(WORKSPACE_DIR.resolve()).as_posix())
    except Exception:
        shutil.rmtree(folder)
        raise
    return imported


def stage_download(relative_path):
    if not relative_path:
        return None
    source = safe_workspace_path(relative_path)
    if not source.is_file():
        raise ValueError('请选择工作区中的文件。')
    if source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('单次下载限 100 MB。')
    folder = DATA_DIR / 'exports' / 'files' / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    shutil.copyfile(source, target)
    return str(target)


# ============================================================
# 本地文件夹导入
# ============================================================

MAX_FOLDER_FILES = 300
MAX_FOLDER_BYTES = 200 * 1024 * 1024

SKIP_DIR_NAMES = {
    ".git", ".svn", ".hg", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".tox", ".next", ".gradle", "node_modules",
    "__pycache__", ".venv", "venv", "dist", "build", "site-packages",
}

SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", ".npmrc", ".pypirc",
    ".netrc", "id_rsa", "id_ed25519", "credentials",
}

SENSITIVE_SUFFIXES = {
    ".pem", ".key", ".p12", ".pfx", ".keystore",
    ".db", ".sqlite", ".sqlite3",
}


def _resolve_source(raw):
    """解析并校验待导入的本地文件夹路径。"""

    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("请先填写要导入的文件夹路径。")

    source = Path(text).expanduser().resolve()

    if not source.exists():
        raise ValueError("路径不存在，请检查后重试。")
    if not source.is_dir():
        raise ValueError("请输入文件夹路径，而不是单个文件。")

    workspace = WORKSPACE_DIR.resolve()

    # 导入工作区自身会造成边扫描边复制的无限递归
    if source == workspace or workspace in source.parents:
        raise ValueError("不能导入工作区自身或其子目录。")

    # 项目根目录含 .env（API key）与会话数据库
    if source == BASE_DIR.resolve():
        raise ValueError("不能导入项目根目录（含密钥与数据文件）。")

    return source


def _collect_files(source):
    """收集待复制文件；跳过符号链接、依赖目录与敏感文件。"""

    files = []
    total = 0
    skipped = 0

    for root, dirs, names in os.walk(source, followlinks=False):

        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIR_NAMES
            and not os.path.islink(os.path.join(root, d))
        ]

        for name in sorted(names):

            path = Path(root) / name

            if path.is_symlink() or not path.is_file():
                continue

            lower = name.lower()
            suffix = path.suffix.lower()

            if (
                lower in SENSITIVE_NAMES
                or lower.startswith(".env")
                or suffix in SENSITIVE_SUFFIXES
            ):
                skipped += 1
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue

            files.append((path, size))
            total += size

    return files, total, skipped


def scan_folder(raw):
    """干跑：只统计，不复制。"""

    source = _resolve_source(raw)
    files, total, skipped = _collect_files(source)

    return {
        "source": str(source),
        "count": len(files),
        "bytes": total,
        "skipped": skipped,
        "over_limit": (
            len(files) > MAX_FOLDER_FILES
            or total > MAX_FOLDER_BYTES
        ),
    }


def import_folder(raw):
    """把本地文件夹复制进 workspace/imports/<原名>-<随机后缀>。"""

    source = _resolve_source(raw)
    files, total, skipped = _collect_files(source)

    if not files:
        raise ValueError("该文件夹没有可导入的文件（可能已被全部过滤）。")

    if len(files) > MAX_FOLDER_FILES:
        raise ValueError(
            f"文件过多：{len(files)} 个，上限 {MAX_FOLDER_FILES} 个。"
        )

    if total > MAX_FOLDER_BYTES:
        raise ValueError(
            f"体积过大：{total / 1048576:.0f} MB，上限 200 MB。"
        )

    dest = safe_workspace_path(
        "imports/" + source.name + "-" + uuid.uuid4().hex[:6]
    )

    try:
        for path, _size in files:
            target = dest / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    root_relative = dest.relative_to(
        WORKSPACE_DIR.resolve()
    ).as_posix()

    paths = [
        (dest / path.relative_to(source))
        .relative_to(WORKSPACE_DIR.resolve())
        .as_posix()
        for path, _size in files
    ]

    return root_relative, paths, len(files), total, skipped
