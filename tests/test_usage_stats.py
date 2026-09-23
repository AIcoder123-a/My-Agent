"""用量统计的回归测试。

覆盖：
- Usage 对象 / dict 两种输入
- 会话级与全局级分别累加、互不串味
- 增量记账（_finalize_result 会被多次调用，不能重复计数）
"""
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from unittest.mock import patch

import usage_stats
from agent_service import AgentService


class FakeUsage:
    def __init__(self, requests=1, input_tokens=100,
                 output_tokens=50, total_tokens=150):
        self.requests = requests
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


class UsageStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / 'usage.json'
        self.patch = patch.object(usage_stats, 'USAGE_FILE', self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_extract_from_object(self):
        extracted = usage_stats.extract_usage(FakeUsage())
        self.assertEqual(1, extracted['requests'])
        self.assertEqual(150, extracted['total_tokens'])

    def test_extract_from_dict(self):
        extracted = usage_stats.extract_usage({'total_tokens': 42})
        self.assertEqual(42, extracted['total_tokens'])
        self.assertEqual(0, extracted['requests'])

    def test_extract_survives_missing_fields(self):
        extracted = usage_stats.extract_usage(object())
        self.assertEqual(0, extracted['total_tokens'])

    def test_record_accumulates(self):
        usage_stats.record_usage('s1', FakeUsage())
        usage_stats.record_usage('s1', FakeUsage())

        session = usage_stats.get_session_usage('s1')
        self.assertEqual(2, session['requests'])
        self.assertEqual(300, session['total_tokens'])

    def test_sessions_are_isolated(self):
        usage_stats.record_usage('s1', FakeUsage(total_tokens=100))
        usage_stats.record_usage('s2', FakeUsage(total_tokens=7))

        self.assertEqual(100, usage_stats.get_session_usage('s1')['total_tokens'])
        self.assertEqual(7, usage_stats.get_session_usage('s2')['total_tokens'])

    def test_total_spans_sessions(self):
        usage_stats.record_usage('s1', FakeUsage(total_tokens=100))
        usage_stats.record_usage('s2', FakeUsage(total_tokens=7))

        self.assertEqual(107, usage_stats.get_total_usage()['total_tokens'])

    def test_count_task(self):
        usage_stats.record_usage('s1', FakeUsage())
        usage_stats.count_task('s1')
        usage_stats.count_task('s1')

        self.assertEqual(2, usage_stats.get_session_usage('s1')['tasks'])
        self.assertEqual(2, usage_stats.get_total_usage()['tasks'])

    def test_empty_usage_is_not_recorded(self):
        usage_stats.record_usage('s1', FakeUsage(0, 0, 0, 0))
        self.assertEqual(0, usage_stats.get_total_usage()['total_tokens'])

    def test_format_usage(self):
        text = usage_stats.format_usage({
            'requests': 3,
            'input_tokens': 2500,
            'output_tokens': 1500,
            'total_tokens': 4000,
            'tasks': 2,
        })
        self.assertIn('3 次', text)
        self.assertIn('4.0K', text)

    def test_format_usage_empty(self):
        self.assertIn(
            '暂无',
            usage_stats.format_usage(usage_stats._empty_stats()),
        )


class IncrementalAccountingTests(unittest.TestCase):
    """_finalize_result 每轮都会调用，必须只记增量。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / 'usage.json'
        self.patch = patch.object(usage_stats, 'USAGE_FILE', self.file)
        self.patch.start()

        self.service = AgentService.__new__(AgentService)
        self.service.lock = RLock()
        self.service.session_id = 's1'
        self.service._task_usage_baseline = {}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def result_with(self, total):
        class _Result:
            def __init__(self, usage):
                self.context_wrapper = type(
                    'CW', (), {'usage': usage}
                )()
        return _Result(FakeUsage(total_tokens=total))

    def test_repeated_finalize_counts_once(self):
        service = self.service

        service._record_usage(self.result_with(100))
        service._record_usage(self.result_with(100))
        service._record_usage(self.result_with(150))

        self.assertEqual(
            150,
            usage_stats.get_session_usage('s1')['total_tokens'],
            '同一份累计值重复上报不应翻倍',
        )

    def test_new_task_resets_baseline(self):
        service = self.service

        service._record_usage(self.result_with(100))
        service._task_usage_baseline = {}
        service._record_usage(self.result_with(100))

        self.assertEqual(
            200,
            usage_stats.get_session_usage('s1')['total_tokens'],
            '新任务重置基线后应正常累加',
        )

    def test_missing_context_wrapper_is_safe(self):
        self.assertEqual(
            {},
            self.service._record_usage(object()),
        )


if __name__ == '__main__':
    unittest.main()
