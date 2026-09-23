"""上下文体积预算的回归测试。

重点覆盖原先设计的盲区：
条目数远低于 48 / 80，但体积早已超出窗口。
"""
import unittest

from token_budget import (
    RECENT_CONTEXT_ITEMS,
    RECENT_CONTEXT_TOKENS,
    SUMMARY_TRIGGER_ITEMS,
    SUMMARY_TRIGGER_TOKENS,
    estimate_item_tokens,
    estimate_items_tokens,
    estimate_tokens,
    token_cutoff,
)


def big_output(size=32000):
    """模拟一条接近上限的 Tool Output。"""
    return {
        'type': 'tool_output',
        'call_id': 'call_x',
        'output': 'x' * size,
    }


def small_message(text='你好'):
    return {'type': 'message', 'role': 'user', 'content': text}


class EstimateTests(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(0, estimate_tokens(None))
        self.assertEqual(0, estimate_tokens(''))

    def test_estimate_scales_with_length(self):
        self.assertGreater(
            estimate_tokens('a' * 3000),
            estimate_tokens('a' * 300),
        )

    def test_estimate_is_positive_for_tiny_text(self):
        self.assertGreaterEqual(estimate_tokens('a'), 1)

    def test_item_estimation_counts_all_fields(self):
        small = estimate_item_tokens({'output': 'a' * 300})
        large = estimate_item_tokens({'output': 'a' * 300, 'extra': 'b' * 300})
        self.assertGreater(large, small)


class TokenCutoffTests(unittest.TestCase):
    def test_empty_items(self):
        self.assertEqual(0, token_cutoff([], RECENT_CONTEXT_TOKENS))

    def test_small_history_kept_entirely(self):
        items = [small_message() for _ in range(30)]
        self.assertEqual(0, token_cutoff(items, RECENT_CONTEXT_TOKENS))

    def test_single_huge_item_still_keeps_last_one(self):
        """单条就超预算时，至少保留最后一条，不能把上下文清空。"""
        items = [big_output()]
        boundary = token_cutoff(items, RECENT_CONTEXT_TOKENS)
        self.assertLess(boundary, len(items))
        self.assertEqual(len(items) - 1, boundary)

    def test_huge_outputs_are_trimmed(self):
        """回归核心：20 条大 Tool Output。

        条目数 20 < RECENT_CONTEXT_ITEMS(48)
              < SUMMARY_TRIGGER_ITEMS(80)，
        旧的纯条目逻辑会原样全部保留 —— 足以撑爆 128K 窗口。
        """
        items = [big_output() for _ in range(20)]

        self.assertLess(
            len(items), RECENT_CONTEXT_ITEMS,
            '前提：条目数确实没到旧的触发线',
        )

        boundary = token_cutoff(items, RECENT_CONTEXT_TOKENS)
        kept = items[boundary:]

        self.assertGreater(
            boundary, 0,
            '体积越线时必须切掉前面的历史',
        )
        self.assertLessEqual(
            estimate_items_tokens(kept),
            RECENT_CONTEXT_TOKENS,
            '保留部分的估算体积必须落在预算内',
        )

    def test_kept_tokens_never_exceed_budget(self):
        """任意混合规模下，保留部分的体积都不应超预算。"""
        items = (
            [small_message() for _ in range(10)]
            + [big_output(9000) for _ in range(12)]
            + [small_message('最后一轮')]
        )

        boundary = token_cutoff(items, RECENT_CONTEXT_TOKENS)

        self.assertLessEqual(
            estimate_items_tokens(items[boundary:]),
            RECENT_CONTEXT_TOKENS,
        )

    def test_trigger_threshold_relationship(self):
        """体积触发线必须高于保留预算，否则会反复压缩。"""
        self.assertGreater(
            SUMMARY_TRIGGER_TOKENS,
            RECENT_CONTEXT_TOKENS,
        )
        self.assertGreaterEqual(
            SUMMARY_TRIGGER_ITEMS,
            RECENT_CONTEXT_ITEMS,
        )


if __name__ == '__main__':
    unittest.main()
