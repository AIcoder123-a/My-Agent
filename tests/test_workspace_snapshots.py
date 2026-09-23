"""workspace 文件快照与撤销。

写入前存旧内容，改坏了能回来。
这些用例直接读写临时目录，不碰用户的 workspace。
"""

import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import workspace_snapshots


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self._stack = tempfile.TemporaryDirectory()
        self.tmp = Path(self._stack.name)

        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()

        self.store = self.tmp / "snapshots"

        self._patchers = [
            patch.object(
                workspace_snapshots,
                "WORKSPACE_DIR",
                self.workspace,
            ),
            patch.object(
                workspace_snapshots,
                "SNAPSHOT_DIR",
                self.store,
            ),
            patch.object(
                workspace_snapshots,
                "BLOB_DIR",
                self.store / "blobs",
            ),
            patch.object(
                workspace_snapshots,
                "INDEX_FILE",
                self.store / "index.json",
            ),
        ]

        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        self._stack.cleanup()

    def write(self, relative, text):
        target = self.workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    # ---------------- 基本往返 ----------------

    def test_modified_file_is_restored(self):
        target = self.write("a.txt", "原始内容")

        workspace_snapshots.capture("a.txt")
        target.write_text("被 Agent 改坏了", encoding="utf-8")

        message = workspace_snapshots.undo_last()

        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "原始内容",
        )
        self.assertIn("已恢复", message)

    def test_undo_deletes_file_that_did_not_exist(self):
        # 关键顺序：文件还不存在的时候 capture，
        # 之后才被 Agent 新建出来。撤销应当把它删掉。
        workspace_snapshots.capture("new.txt")

        self.write("new.txt", "新建的内容")
        self.assertTrue(
            (self.workspace / "new.txt").exists()
        )

        workspace_snapshots.undo_last()

        self.assertFalse(
            (self.workspace / "new.txt").exists()
        )

    def test_undo_is_lifo(self):
        target = self.write("b.txt", "第一版")

        workspace_snapshots.capture("b.txt")
        target.write_text("第二版", encoding="utf-8")

        workspace_snapshots.capture("b.txt")
        target.write_text("第三版", encoding="utf-8")

        workspace_snapshots.undo_last()
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "第二版",
        )

        workspace_snapshots.undo_last()
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "第一版",
        )

    def test_undo_twice_says_nothing_left(self):
        self.write("c.txt", "x")
        workspace_snapshots.capture("c.txt")

        workspace_snapshots.undo_last()

        self.assertIn(
            "没有可撤销",
            workspace_snapshots.undo_last(),
        )

    # ---------------- 边界 ----------------

    def test_path_outside_workspace_is_not_captured(self):
        record = workspace_snapshots.capture(
            "../outside.txt"
        )
        self.assertIsNone(record)

    def test_restore_rejects_unknown_id(self):
        with self.assertRaises(ValueError):
            workspace_snapshots.restore("nope")

    # ---------------- 索引与清理 ----------------

    def test_list_changes_is_newest_first(self):
        self.write("d.txt", "1")

        workspace_snapshots.capture(
            "d.txt", action="write_file"
        )
        workspace_snapshots.capture(
            "d.txt", action="edit_file"
        )

        changes = workspace_snapshots.list_changes()

        self.assertEqual(len(changes), 2)
        self.assertEqual(
            changes[0]["action"], "edit_file"
        )
        self.assertEqual(
            changes[1]["action"], "write_file"
        )

    def test_index_is_capped(self):
        original = workspace_snapshots.MAX_RECORDS

        workspace_snapshots.MAX_RECORDS = 5

        try:
            for index in range(20):
                self.write(f"f{index}.txt", str(index))
                workspace_snapshots.capture(
                    f"f{index}.txt"
                )

            records = workspace_snapshots._load_index()

            self.assertEqual(len(records), 5)

            blobs = list(
                (self.store / "blobs").glob("*.blob")
            )
            self.assertEqual(len(blobs), 5)

        finally:
            workspace_snapshots.MAX_RECORDS = original

    def test_session_filter(self):
        self.write("e.txt", "1")

        workspace_snapshots.capture(
            "e.txt", session_id="s1"
        )
        workspace_snapshots.capture(
            "e.txt", session_id="s2"
        )

        self.assertEqual(
            len(
                workspace_snapshots.list_changes(
                    session_id="s1"
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                workspace_snapshots.list_changes()
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
