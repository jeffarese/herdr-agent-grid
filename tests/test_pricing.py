from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from herdr_agent_grid.pricing import estimate
from herdr_agent_grid.telemetry import Cursor, Metrics, consume, cost_label


class PricingTests(unittest.TestCase):
    def test_claude_cache_ttls_and_reported_modifiers(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 2_000_000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000},
                 "output_tokens_details": {"thinking_tokens": 500_000}}
        cost, _ = estimate("claude", "claude-opus-5-5", usage)
        self.assertAlmostEqual(cost, 37.2)
        cost, _ = estimate("claude", "claude-opus-5-5", dict(usage, speed="fast", inference_geo="us"))
        self.assertAlmostEqual(cost, 37.2 * 2 * 1.1)

    def test_codex_cache_is_part_of_input_and_reasoning_is_part_of_output(self):
        usage = {"input_tokens": 1_000_000, "cached_input_tokens": 700_000,
                 "cache_write_input_tokens": 100_000, "output_tokens": 100_000,
                 "reasoning_output_tokens": 80_000}
        cost, _ = estimate("codex", "gpt-6.1-sol", usage, context_input=200_000)
        self.assertAlmostEqual(cost, .4 + .07 + .25 + 1)
        cost, _ = estimate("codex", "gpt-6.1-sol", usage, context_input=300_000)
        self.assertAlmostEqual(cost, (.4 + .07 + .25) * 2 + 1.5)

    def test_unknown_models_and_incomplete_usage_do_not_create_false_zero(self):
        self.assertIsNone(estimate("claude", "claude-opus-future", {"input_tokens": 5, "output_tokens": 1})[0])
        self.assertIsNone(estimate("codex", "gpt-6.1-sol", {"total_tokens": 10})[0])
        self.assertIsNone(estimate("codex", "gpt-6.1-sol", {"input_tokens": 5, "cached_input_tokens": 6, "output_tokens": 1})[0])

    def test_claude_streams_replace_usage_and_different_models_keep_their_prices(self):
        cursor = Cursor(Path("unused"))
        record = {"type": "assistant", "message": {"id": "one", "model": "claude-opus-5-5",
                  "usage": {"input_tokens": 1000, "output_tokens": 1000}, "content": []}}
        consume(cursor, record, "claude")
        consume(cursor, record, "claude")
        self.assertAlmostEqual(cursor.metrics.estimated_cost, .024)
        record["message"]["usage"]["output_tokens"] = 2000
        consume(cursor, record, "claude")
        self.assertAlmostEqual(cursor.metrics.estimated_cost, .044)
        other = deepcopy(record)
        other["message"].update(id="two", model="claude-sonnet-5-5")
        consume(cursor, other, "claude")
        self.assertAlmostEqual(cursor.metrics.estimated_cost, .044 + .022)
        other["message"].update(id="three", model="unknown-model")
        consume(cursor, other, "claude")
        self.assertTrue(cursor.metrics.estimate_partial)
        self.assertTrue(cost_label(cursor.metrics).startswith("≥~$"))
        consume(cursor, {"type": "cost-state", "totalCostUSD": .5}, "claude")
        self.assertEqual(cost_label(cursor.metrics), "$0.50")
        self.assertEqual(cost_label(Metrics(cost=0, estimated_cost=10)), "$0.00")

    def test_codex_cumulative_records_deduplicate_and_price_model_deltas(self):
        cursor = Cursor(Path("unused"))
        consume(cursor, {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}, "codex")
        record = {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 100, "total_tokens": 1100},
            "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 100}}}}
        consume(cursor, record, "codex")
        consume(cursor, record, "codex")
        self.assertAlmostEqual(cursor.metrics.estimated_cost, .00205)
        consume(cursor, {"type": "turn_context", "payload": {"model": "gpt-6-astra"}}, "codex")
        record["payload"]["info"]["total_token_usage"].update(input_tokens=2000, cached_input_tokens=1000, output_tokens=200, total_tokens=2200)
        consume(cursor, record, "codex")
        self.assertAlmostEqual(cursor.metrics.estimated_cost, .00205 + .0105)

    def test_truncated_codex_prices_only_observed_usage(self):
        cursor = Cursor(Path("unused"), truncated=True)
        consume(cursor, {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}, "codex")
        consume(cursor, {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 10_000_000, "output_tokens": 500_000, "total_tokens": 10_500_000},
            "last_token_usage": {"input_tokens": 1000, "output_tokens": 100}}}}, "codex")
        self.assertAlmostEqual(cursor.metrics.estimated_cost, .003)
        self.assertTrue(cursor.metrics.estimate_partial)
        self.assertEqual(cursor.metrics.tokens, 10_500_000)

    def test_latest_message_ignores_private_reasoning_sidechains_and_tool_output(self):
        cursor = Cursor(Path("unused"))
        consume(cursor, {"type": "assistant", "timestamp": "2026-10-04T10:00:00Z", "message": {
            "content": [{"type": "thinking", "thinking": "private thought"},
                        {"type": "text", "text": "I am checking\npermissions."}]}}, "claude")
        self.assertEqual(cursor.metrics.last_message, "I am checking permissions.")
        consume(cursor, {"type": "assistant", "isSidechain": True, "message": {
            "content": [{"type": "text", "text": "Another agent"}]}}, "claude")
        self.assertEqual(cursor.metrics.last_message, "I am checking permissions.")
        consume(cursor, {"type": "response_item", "payload": {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Checks passed."}]}}, "codex")
        consume(cursor, {"type": "response_item", "payload": {"type": "function_call_output", "output": "private tool output"}}, "codex")
        self.assertEqual(cursor.metrics.last_message, "Checks passed.")
        self.assertNotIn("private", repr(cursor.metrics))


if __name__ == "__main__":
    unittest.main()
