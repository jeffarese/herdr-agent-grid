from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from herdr_agent_grid.model import Agent
from herdr_agent_grid.telemetry import Cursor, Metrics, Telemetry, consume, cost_label, screen_call, token_label
from herdr_agent_grid.demo import demo_state
from herdr_agent_grid.view import View, plain_frame


class TelemetryTests(unittest.TestCase):
    def test_claude_streaming_usage_replaces_message_totals_and_tracks_tool_return(self):
        cursor = Cursor(Path("unused"))
        record = {"type": "assistant", "timestamp": "2026-10-04T10:00:00Z", "effort": "medium",
                  "message": {"id": "message1", "model": "model", "usage": {
                      "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80},
                      "content": [{"type": "tool_use", "id": "tool1", "name": "Read",
                                   "input": {"file_path": "src/app.py"}}]}}
        consume(cursor, record, "claude")
        record["message"]["usage"]["output_tokens"] = 30
        consume(cursor, record, "claude")
        self.assertEqual(cursor.metrics.tokens, 210)
        self.assertEqual(cursor.metrics.effort, "medium")
        record["perTurnEffort"] = "high"
        consume(cursor, record, "claude")
        self.assertEqual(cursor.metrics.effort, "high")
        self.assertEqual(cursor.metrics.last_call, "Read")
        self.assertFalse(cursor.metrics.call_done)
        consume(cursor, {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tool1"}]}}, "claude")
        self.assertTrue(cursor.metrics.call_done)
        self.assertEqual(cursor.metrics.call_detail, "src/app.py")

    def test_claude_cost_and_cumulative_usage_are_reported_not_guessed(self):
        cursor = Cursor(Path("unused"))
        consume(cursor, {"type": "cost-state", "totalCostUSD": 1.25,
                         "hasUnknownModelCost": True, "startTime": 1791108000000,
                         "modelUsage": {"model": {"inputTokens": 100, "outputTokens": 20,
                                                  "cacheReadInputTokens": 300, "cacheCreationInputTokens": 50,
                                                  "thinkingTokens": 10}}}, "claude")
        self.assertEqual(cursor.metrics.tokens, 470)
        self.assertEqual(cursor.metrics.cost, 1.25)
        self.assertEqual(cost_label(cursor.metrics), "≥$1.25")
        self.assertEqual(cursor.metrics.started_at, 1791108000)
        consume(cursor, {"type": "cost-state", "totalCostUSD": 0}, "claude")
        self.assertEqual(cost_label(cursor.metrics), "$0.00")
        self.assertEqual(cost_label(Metrics()), "—")

    def test_codex_cumulative_tokens_are_replaced_and_cost_remains_unknown(self):
        cursor = Cursor(Path("unused"))
        consume(cursor, {"type": "turn_context", "payload": {
            "model": "gpt-example", "effort": "xhigh"}}, "codex")
        self.assertEqual(cursor.metrics.effort, "xhigh")
        consume(cursor, {"type": "turn_context", "payload": {
            "model": "gpt-example", "effort": None}}, "codex")
        self.assertEqual(cursor.metrics.effort, "")
        consume(cursor, {"type": "response_item", "timestamp": "2026-10-04T10:00:00Z",
                         "payload": {"type": "function_call", "name": "exec_command", "call_id": "call1",
                                     "arguments": '{"cmd":"secret command", "description":"Run checks"}'}}, "codex")
        self.assertEqual(cursor.metrics.call_detail, "Run checks")
        self.assertNotIn("secret", cursor.metrics.call_detail)
        consume(cursor, {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": 120, "reasoning_output_tokens": 30}}}}, "codex")
        consume(cursor, {"type": "token_usage_record", "payload": {
            "thread_token_usage": {"total_tokens": 150}, "usage": {"total_tokens": 30}}}, "codex")
        self.assertEqual(cursor.metrics.tokens, 150)
        self.assertIsNone(cursor.metrics.cost)
        consume(cursor, {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call1", "output": "private output"}}, "codex")
        self.assertTrue(cursor.metrics.call_done)

    def test_incremental_append_partial_line_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            cursor = Cursor(path)
            first = {"type": "response_item", "payload": {"type": "custom_tool_call",
                     "name": "apply_patch", "call_id": "one"}, "timestamp": "2026-10-04T10:00:00Z"}
            path.write_text(json.dumps(first) + "\n")
            Telemetry.update(cursor, "codex")
            self.assertEqual(cursor.metrics.last_call, "apply_patch")
            second = {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "one"}}
            with path.open("a") as f:
                f.write(json.dumps(second))
            Telemetry.update(cursor, "codex")
            self.assertFalse(cursor.metrics.call_done)
            with path.open("a") as f:
                f.write("\n")
            Telemetry.update(cursor, "codex")
            self.assertTrue(cursor.metrics.call_done)
            path.write_text('{"type":"session_meta","payload":{}}\n')
            Telemetry.update(cursor, "codex")
            self.assertEqual(cursor.metrics.last_call, "")
            self.assertIsNone(cursor.metrics.tokens)

    def test_large_claude_tail_marks_reconstructed_usage_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            record = {"type": "assistant", "message": {"id": "last", "usage": {"input_tokens": 5}}}
            path.write_text('{"type":"ignored"}\n' * 100 + json.dumps(record) + "\n")
            cursor = Cursor(path)
            with patch("herdr_agent_grid.telemetry.MAX_READ", 200):
                Telemetry.update(cursor, "claude")
            self.assertTrue(cursor.metrics.tokens_partial)
            self.assertEqual(token_label(cursor.metrics), "≥5")

    def test_only_exact_session_match_is_used_and_paths_are_constrained(self):
        sid = "11111111-1111-1111-1111-111111111111"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "projects"
            project = root / "project"
            project.mkdir(parents=True)
            path = project / (sid + ".jsonl")
            path.write_text('{"type":"cost-state","totalCostUSD":2.5}\n')
            agent = Agent("w1:p1", "claude", "a", "t", "w", "working", "claude", "id", sid)
            telemetry = Telemetry()
            with patch("herdr_agent_grid.telemetry.roots", return_value=[root]):
                self.assertEqual(telemetry.read(agent).cost, 2.5)
                other = replace(agent, session_ref="22222222-2222-2222-2222-222222222222")
                self.assertIsNone(telemetry.read(other).cost)
                self.assertEqual(telemetry.read(other).cost_reason, "No matching local session log")
                self.assertIsNone(telemetry.find(replace(agent, session_ref="*")))
                outside = Path(directory) / "outside.jsonl"
                outside.write_text(path.read_text())
                self.assertIsNone(telemetry.find(replace(agent, session_kind="path", session_ref=str(outside))))
            telemetry.forget([other])
            self.assertNotIn(agent.identity, telemetry.cursors)

    def test_status_duration_is_observed_and_resets_for_a_replaced_session(self):
        agent = Agent("w1:p1", "codex", "a", "t", "w", "working", terminal_id="old")
        telemetry = Telemetry()
        with patch("time.time", return_value=100):
            self.assertEqual(telemetry.read(agent).seen_at, 100)
        with patch("time.time", return_value=120):
            m = telemetry.read(replace(agent, status="blocked"))
            self.assertEqual((m.seen_at, m.status_since), (100, 120))
        with patch("time.time", return_value=130):
            new = telemetry.read(replace(agent, terminal_id="new"))
            self.assertEqual(new.seen_at, 130)

    def test_replacing_an_agent_drops_previous_terminal_activity(self):
        from herdr_agent_grid.client import HerdrError
        from herdr_agent_grid.refresh import Refresher
        class Fake:
            replaced = False
            def snapshot(self):
                return {"agents": [{"pane_id": "w1:p1", "agent": "gemini" if self.replaced else "codex"}]}
            def read(self, pane, lines):
                if self.replaced:
                    raise HerdrError("read unavailable")
                return "● Bash(checks)"
        client = Fake()
        worker = Refresher(client, interval=.03)
        worker.request({"w1:p1": 40})
        worker.start()
        def wait_for(predicate):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if predicate(worker.get()):
                    return
                time.sleep(.01)
            self.fail("refresh condition timed out")
        try:
            wait_for(lambda s: s.metrics.get("w1:p1", Metrics()).last_call == "Bash")
            client.replaced = True
            worker.request({"w1:p1": 40}, force=True)
            wait_for(lambda s: s.agents and s.agents[0].kind == "gemini" and "w1:p1" in s.errors)
            self.assertEqual(worker.get().metrics["w1:p1"].last_call, "")
            self.assertNotIn("w1:p1", worker.get().previews)
        finally:
            worker.close()
            worker.thread.join(2)

    def test_dashboard_has_metrics_but_no_terminal_transcript(self):
        state = demo_state()
        frame = plain_frame(View().draw(state, 140, 38), 140, 38)
        self.assertIn("▸ Edit", frame)
        self.assertIn("API COST", frame)
        self.assertIn("$1.24", frame)
        self.assertIn("! NEEDS INPUT", frame)
        self.assertNotIn("grid-template-columns", frame)
        self.assertNotIn("Waiting for your decision", frame)
        self.assertEqual(screen_call("Some unrelated message\nCURRENT PROMPT"), "")
        self.assertEqual(screen_call("⏺ Bash(python3 -m pytest)\nCURRENT PROMPT"), "Bash")

    def test_overview_deduplicates_shared_session_cost(self):
        state = demo_state()
        state.agents = [replace(state.agents[0], pane_id="w1:p1", session_kind="id", session_ref="same"),
                        replace(state.agents[0], pane_id="w1:p2", session_kind="id", session_ref="same")]
        state.metrics = {a.pane_id: Metrics(cost=1.0, tokens=100) for a in state.agents}
        frame = plain_frame(View().draw(state, 140, 38), 140, 38)
        self.assertIn("API cost $1.00 (1/1 reported)", frame)


if __name__ == "__main__":
    unittest.main()
