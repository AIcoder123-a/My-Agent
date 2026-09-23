"""workspace 文件的改动快照与撤销。

Agent 写文件是裸覆盖：write_file 直接 write_text，edit_file 也是原地替换。
一旦 Agent 改错东西，用户只能自己从记忆里拼回原文。

这里在每次写入之前把旧内容存一份，因此可以「撤销上一次改动」。

边界（必须说清楚）：
只覆盖经过 write_file / edit_file 的写入。Agent 用终端（run_shell）
改的文件拦不住，也不在快照里。这是静态拦截做不到的部分。
"""

import json
import os
import shutil
import tempfile
import threading
import uuid

from datetime import datetime
from pathlib import Path

from paths import WORKSPACE_DIR


PROJECT_ROOT = Path(__file__).resolve().parent

SNAPSHOT_DIR = PROJECT_ROOT / "data" / "snapshots"

BLOB_DIR = SNAPSHOT_DIR / "blobs"

INDEX_FILE = SNAPSHOT_DIR / "index.json"

# 索引里最多保留多少条记录。
# 超出的记录连同它的 blob 一起删除，避免快照无限增长。
MAX_RECORDS = 200

_lock = threading.Lock()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []

    try:
        with INDEX_FILE.open("r", encoding="utf-8") as stream:
            data = json.load(stream)

    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return data


def _save_index(records: list[dict]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=SNAPSHOT_DIR,
        prefix=".index-",
        suffix=".tmp",
        delete=False,
    )

    try:
        with handle:
            json.dump(
                records,
                handle,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(handle.name, INDEX_FILE)

    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _blob_path(record_id: str) -> Path:
    return BLOB_DIR / f"{record_id}.blob"


def capture(
    relative_path: str,
    session_id: str | None = None,
    action: str = "write_file",
) -> dict | None:
    """写入前把目标文件的当前内容存一份。

    返回快照记录；文件本来就不存在时同样记录一条（existed=False），
    这样撤销的时候能把新建的文件删掉。
    """

    workspace = WORKSPACE_DIR.resolve()

    try:
        target = (workspace / relative_path).resolve()
        target.relative_to(workspace)

    except (ValueError, OSError):
        # 不在 workspace 内的路径不纳入快照
        return None

    record = {
        "id": uuid.uuid4().hex[:12],
        "time": _now_text(),
        "path": relative_path,
        "action": action,
        "session_id": session_id,
        "existed": target.is_file(),
        "size": 0,
        "undone": False,
    }

    with _lock:
        if record["existed"]:
            try:
                BLOB_DIR.mkdir(parents=True, exist_ok=True)

                shutil.copyfile(
                    target,
                    _blob_path(record["id"]),
                )

                record["size"] = target.stat().st_size

            except OSError:
                # 存不下快照不应该阻断正常写入
                return None

        records = _load_index()
        records.append(record)

        # _prune 返回截断后的新列表。
        # 早期版本在这里丢弃了返回值，上限因此从未生效。
        records = _prune(records)

        _save_index(records)

    return record


def _prune(records: list[dict]) -> list[dict]:
    """只保留最近 MAX_RECORDS 条，删除被淘汰记录的 blob。"""
    if len(records) <= MAX_RECORDS:
        return records

    dropped = records[:-MAX_RECORDS]
    kept = records[-MAX_RECORDS:]

    for record in dropped:
        if not record.get("existed"):
            continue

        blob = _blob_path(record["id"])

        try:
            blob.unlink()
        except OSError:
            pass

    return kept


def list_changes(
    limit: int = 20,
    session_id: str | None = None,
    include_undone: bool = False,
) -> list[dict]:
    """最近的改动，新的在前。"""

    records = _load_index()

    if session_id is not None:
        records = [
            r
            for r in records
            if r.get("session_id") == session_id
        ]

    if not include_undone:
        records = [
            r
            for r in records
            if not r.get("undone")
        ]

    return list(reversed(records[-limit:]))


def restore(record_id: str) -> str:
    """把某个快照恢复回 workspace。

    原本存在的文件恢复内容；原本不存在的文件直接删除
    （也就是把「新建」这个动作撤掉）。
    """

    with _lock:
        records = _load_index()

        target_record = None

        for record in records:
            if record.get("id") == record_id:
                target_record = record
                break

        if target_record is None:
            raise ValueError(
                f"找不到快照：{record_id}"
            )

        workspace = WORKSPACE_DIR.resolve()

        try:
            target = (
                workspace / target_record["path"]
            ).resolve()

            target.relative_to(workspace)

        except ValueError:
            raise ValueError(
                "快照路径不在 workspace 内，"
                "拒绝恢复。"
            )

        if target_record.get("existed"):
            blob = _blob_path(record_id)

            if not blob.exists():
                raise FileNotFoundError(
                    "快照内容已丢失，无法恢复："
                    f"{target_record['path']}"
                )

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copyfile(blob, target)

            message = (
                f"已恢复：{target_record['path']}"
            )

        else:
            if target.exists():
                target.unlink()

            message = (
                f"已删除新建的文件："
                f"{target_record['path']}"
            )

        target_record["undone"] = True

        _save_index(records)

        return message


def undo_last(
    session_id: str | None = None,
) -> str:
    """撤销最近一次尚未撤销的改动。"""

    pending = list_changes(
        limit=1,
        session_id=session_id,
    )

    if not pending:
        return "没有可撤销的文件改动。"

    record = pending[0]

    return restore(record["id"])
