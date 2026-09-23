"""审计日志轮转与读取的回归测试。

覆盖原先的隐患：
- tool_audit.jsonl 只增不减，长期运行无限增长
- read_audit_logs 先收集全部再切片，几 MB 日志会整体进内存
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tool_logging


class LogRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / 'tool_audit.jsonl'
        self.patches = [
            patch.object(tool_logging, 'LOG_FILE', self.log),
            patch.object(tool_logging, 'MAX_LOG_BYTES', 1500),
            patch.object(tool_logging, 'MAX_LOG_BACKUPS', 3),
            patch.object(tool_logging, 'SIZE_CHECK_INTERVAL', 1),
        ]
        for item in self.patches:
            item.start()
        tool_logging._write_count = 0

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write_many(self, count, payload='x' * 200):
        for _ in range(count):
            tool_logging.write_log({'event': 'test', 'payload': payload})

    def test_rotation_creates_archive_and_resets_current(self):
        self.write_many(60)

        self.assertTrue(self.log.exists(), '当前日志应被重建')
        self.assertTrue(
            (self.root / 'tool_audit.1.jsonl').exists(),
            '超限后应轮转出 .1 分片',
        )
        self.assertLess(
            self.log.stat().st_size,
            1500,
            '轮转后当前文件应回到限内',
        )

    def test_rotation_keeps_at_most_max_backups(self):
        self.write_many(300)

        self.assertTrue((self.root / 'tool_audit.1.jsonl').exists())
        self.assertTrue((self.root / 'tool_audit.2.jsonl').exists())
        self.assertTrue((self.root / 'tool_audit.3.jsonl').exists())
        self.assertFalse(
            (self.root / 'tool_audit.4.jsonl').exists(),
            '超出保留份数的分片必须被丢弃',
        )

    def test_current_log_stays_readable_after_rotation(self):
        """轮转不能破坏可解析性。

        注意：轮转发生在写入之后，
        所以最后一条可能刚好被移进分片，
        当前文件此刻可以是空的。
        """
        self.write_many(80)

        # 再写一条，保证当前文件非空
        tool_logging.write_log({'event': 'after-rotation'})

        records = tool_logging.read_audit_logs(limit=50)
        self.assertTrue(records)
        self.assertEqual('after-rotation', records[-1]['event'])

        # 分片里的历史也必须能正常解析
        archived = tool_logging.read_audit_logs(
            limit=500, include_archives=True,
        )
        self.assertGreater(len(archived), len(records))
        for record in archived:
            self.assertIn('event', record)

    def test_read_limit_is_respected(self):
        self.write_many(10, payload='short')

        records = tool_logging.read_audit_logs(limit=3)
        self.assertEqual(3, len(records))
        self.assertEqual(
            ['test'] * 3,
            [r['event'] for r in records],
        )

    def test_archives_can_be_read_on_demand(self):
        self.write_many(300)

        current_only = tool_logging.read_audit_logs(limit=10000)
        with_archives = tool_logging.read_audit_logs(
            limit=10000, include_archives=True,
        )

        self.assertGreater(
            len(with_archives),
            len(current_only),
            'include_archives 应能读到轮转出去的历史',
        )

    def test_task_id_filter(self):
        tool_logging.current_task_id.set('task-a')
        self.write_many(3, payload='short')
        tool_logging.current_task_id.set('task-b')
        self.write_many(3, payload='short')

        records = tool_logging.read_audit_logs(
            limit=100, task_id='task-a',
        )

        self.assertEqual(3, len(records))
        self.assertTrue(
            all(r['task_id'] == 'task-a' for r in records)
        )


if __name__ == '__main__':
    unittest.main()
