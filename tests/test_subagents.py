from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from herdr_agent_grid.demo import demo_state
from herdr_agent_grid.model import Agent, cell_width
from herdr_agent_grid.subagents import Subagents
from herdr_agent_grid.telemetry import ChildHint, Cursor, Subagent, Telemetry, consume, cost_label, subagent_time, timestamp
from herdr_agent_grid.view import View, child_row, plain_frame

START = "2026-10-04T10:00:00Z"
END = "2026-10-04T10:01:00Z"


def write(path, *records, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")


def claude_message(message_id="m1", effort="high"):
    return {"type": "assistant", "timestamp": START, "isSidechain": True,
            "effort": effort, "message": {"id": message_id, "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1000, "output_tokens": 100}, "content": []}}


def codex_meta(child_id, parent="parent", name="Raman", guardian=False):
    source = {"other": "guardian"} if guardian else {"thread_spawn": {"parent_thread_id": parent, "agent_nickname": name}}
    return {"type": "session_meta", "timestamp": START, "payload": {
        "id": child_id, "source": {"subagent": source}, "parent_thread_id": parent}}


class SubagentTests(unittest.TestCase):
    def claude_fixture(self, directory):
        path = Path(directory) / "projects" / "project" / "parent.jsonl"
        write(path, {"type": "cost-state", "totalCostUSD": 2})
        agent = Agent("w1:p1", "claude", "Parent", "Task", "Project", "working")
        return agent, Cursor(path), path.with_suffix("") / "subagents"

    def test_claude_metadata_and_sidechain_usage_stay_separate_from_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            path = folder / "agent-child.jsonl"
            write(path, claude_message())
            path.with_suffix(".meta.json").write_text(json.dumps({"description": "API tests", "agentType": "tester"}))
            Telemetry.update(parent, "claude")
            children = Subagents().read(agent, parent)
            self.assertEqual(len(children), 1)
            child = children[0]
            self.assertEqual((child.name, child.model, child.effort), ("API tests", "claude-sonnet-4-6", "high"))
            self.assertIsNotNone(child.estimated_cost)
            self.assertTrue(cost_label(child).startswith("~"))
            self.assertEqual(parent.metrics.cost, 2)
            self.assertIsNone(parent.metrics.tokens)
            consume(parent, claude_message(), "claude")
            self.assertIsNone(parent.metrics.started_at)
            with self.assertRaises(FrozenInstanceError):
                child.name = "Changed"

    def test_claude_spawn_names_completion_and_missing_log(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            consume(parent, {"type": "assistant", "timestamp": START, "message": {"content": [{
                "type": "tool_use", "name": "Agent", "id": "spawn", "input": {
                    "name": "API review", "description": "Long description", "model": "claude-opus-5-5", "effort": "medium"}}]}}, "claude")
            consume(parent, {"type": "user", "timestamp": END, "message": {"content": [{
                "type": "tool_result", "tool_use_id": "spawn"}]}, "toolUseResult": {
                    "agentId": "child", "status": "completed", "totalDurationMs": 42000}}, "claude")
            child = Subagents().read(agent, parent)[0]
            self.assertEqual((child.name, child.model, child.effort), ("API review", "claude-opus-5-5", "medium"))
            self.assertEqual(subagent_time(child, timestamp(END) + 999), "42s")
            self.assertEqual(cost_label(child), "—")

    def test_claude_incremental_usage_replaces_streaming_ids_and_unknown_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            path = folder / "agent-child.jsonl"
            record = claude_message(effort=None)
            write(path, record)
            reader = Subagents()
            first = reader.read(agent, parent)[0]
            record["message"]["usage"]["output_tokens"] = 200
            write(path, record, append=True)
            second = reader.read(agent, parent)[0]
            self.assertEqual(second.effort, "")
            self.assertGreater(second.estimated_cost, first.estimated_cost)
            self.assertEqual(reader.cursors[agent.identity]["child"].metrics.tokens, 1200)
            self.assertEqual(reader.read(agent, parent), (second,))
            reader.forget(set())
            self.assertFalse(reader.cursors)

    def test_claude_meta_tool_link_before_result_and_actual_model_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            consume(parent, {"type": "assistant", "timestamp": START, "message": {"content": [{
                "type": "tool_use", "name": "Task", "id": "spawn", "input": {
                    "name": "Review", "model": "opus", "effort": "medium"}}]}}, "claude")
            path = folder / "agent-child.jsonl"
            write(path, claude_message())
            path.with_suffix(".meta.json").write_text('{"toolUseId":"spawn"}')
            child = Subagents().read(agent, parent)[0]
            self.assertEqual((child.name, child.model, child.effort), ("Review", "claude-sonnet-4-6", "high"))

    def test_claude_async_notification_freezes_time_and_duplicate_delivery_does_not_extend_it(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            parent.child_hints["child"] = ChildHint("Review", started_at=timestamp(START), status="working", event_at=timestamp(START), call_id="spawn")
            write(folder / "agent-child.jsonl", claude_message())
            notice = "<task-notification><task-id>child</task-id><tool-use-id>spawn</tool-use-id><status>completed</status><result>Private result</result></task-notification>"
            consume(parent, {"type": "queue-operation", "operation": "enqueue", "timestamp": END, "content": notice}, "claude")
            consume(parent, {"type": "user", "timestamp": "2026-10-04T10:03:00Z", "message": {"content": [{"type": "text", "text": notice}]}}, "claude")
            child = Subagents().read(agent, parent)[0]
            self.assertEqual(child.status, "done")
            self.assertEqual(subagent_time(child, timestamp(END) + 999), "1m 00s")
            self.assertNotIn("Private result", repr(parent.child_hints))

    def test_claude_end_turn_stops_child_and_resume_reopens_it(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            path = folder / "agent-child.jsonl"
            final = claude_message()
            final["timestamp"], final["message"]["stop_reason"] = END, "end_turn"
            write(path, claude_message(), final)
            reader = Subagents()
            done = reader.read(agent, parent)[0]
            self.assertEqual(done.status, "done")
            self.assertEqual(subagent_time(done, timestamp(END) + 999), "1m 00s")
            write(path, {"type": "user", "timestamp": "2026-10-04T10:02:00Z", "isSidechain": True,
                         "message": {"content": "Synthetic follow-up"}}, append=True)
            resumed = reader.read(agent, parent)[0]
            self.assertEqual(resumed.status, "working")
            self.assertIsNone(resumed.finished_at)

    def test_delayed_old_notification_cannot_finish_a_resumed_claude_child(self):
        cursor = Cursor(Path("unused"))
        cursor.child_hints["child"] = ChildHint("Review", started_at=timestamp(END), status="working", event_at=timestamp(END), call_id="new-spawn")
        notice = "<task-notification><task-id>child</task-id><tool-use-id>old-spawn</tool-use-id><status>completed</status></task-notification>"
        consume(cursor, {"type": "user", "timestamp": "2026-10-04T10:02:00Z", "message": {"content": notice}}, "claude")
        self.assertEqual(cursor.child_hints["child"].status, "working")
        self.assertIsNone(cursor.child_hints["child"].finished_at)

    def test_nested_child_completion_is_read_from_its_owner_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            write(folder / "agent-child.jsonl", claude_message())
            write(folder / "agent-owner.jsonl", {"type": "user", "timestamp": END, "isSidechain": True,
                "message": {"content": []}, "toolUseResult": {"agentId": "child", "status": "completed", "totalDurationMs": 42000}})
            children = Subagents().read(agent, parent)
            child = next(child for child in children if child.id == "child")
            self.assertEqual(child.status, "done")
            self.assertEqual(subagent_time(child, timestamp(END) + 999), "42s")

    def test_running_children_sort_before_old_finished_files_and_unknown_stays_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            done = claude_message()
            done["timestamp"], done["message"]["stop_reason"] = END, "end_turn"
            write(folder / "agent-a-old.jsonl", claude_message(), done)
            write(folder / "agent-z-working.jsonl", claude_message())
            write(folder / "agent-unknown.jsonl", {"type": "attachment", "timestamp": START})
            children = Subagents().read(agent, parent)
            self.assertEqual([c.status for c in children], ["working", "unknown", "done"])
            self.assertEqual(children[0].id, "z-working")

    def test_claude_other_parent_and_outside_symlinks_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            outside = Path(directory) / "outside.jsonl"
            write(outside, claude_message())
            write(folder.parent.parent / "unrelated" / "subagents" / "agent-other.jsonl", claude_message())
            folder.mkdir(parents=True)
            (folder / "agent-escape.jsonl").symlink_to(outside)
            self.assertEqual(Subagents().read(agent, parent), ())

    def test_child_read_failure_preserves_parent_and_cached_child(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            write(folder / "agent-child.jsonl", claude_message())
            reader = Subagents()
            first = reader.read(agent, parent)
            with patch.object(Telemetry, "update", side_effect=OSError("temporarily unreadable")):
                self.assertEqual(reader.read(agent, parent), first)

    def test_codex_explicit_parent_names_cost_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "date" / "child.jsonl"
            write(path, codex_meta("child"), {"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "xhigh"}},
                  {"type": "token_usage_record", "payload": {"thread_token_usage": {
                      "input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}}},
                  {"type": "event_msg", "timestamp": END, "payload": {"type": "task_complete"}})
            write(root / "other.jsonl", codex_meta("other", parent="unrelated"))
            write(root / "guardian.jsonl", codex_meta("guardian", guardian=True))
            agent = Agent("w1:p1", "codex", "Parent", "Task", "Project", "working")
            parent = Cursor(root / "parent.jsonl", session_id="parent")
            reader = Subagents()
            with patch("herdr_agent_grid.subagents.roots", return_value=[root]):
                children = reader.read(agent, parent)
                self.assertEqual(len(children), 1)
                child = children[0]
                self.assertEqual((child.name, child.model, child.effort), ("Raman", "gpt-6.1-sol", "xhigh"))
                self.assertIsNotNone(child.estimated_cost)
                self.assertEqual(subagent_time(child, timestamp(END) + 999), "1m 00s")
                write(path, {"type": "event_msg", "timestamp": END, "payload": {"type": "task_started"}}, append=True)
                resumed = reader.read(agent, parent)[0]
                self.assertIsNone(resumed.finished_at)
                self.assertEqual(subagent_time(resumed, timestamp(END) + 30), "1m 30s")

    def test_codex_index_reuses_headers_and_discovers_new_and_partial_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "child.jsonl"
            write(path, codex_meta("child"))
            reader = Subagents()
            with patch("herdr_agent_grid.subagents.roots", return_value=[root]), patch("herdr_agent_grid.subagents.read_object", wraps=__import__("herdr_agent_grid.subagents", fromlist=["read_object"]).read_object) as reads:
                reader.index_codex()
                self.assertEqual(reads.call_count, 1)
                write(path, {"type": "event_msg", "payload": {"type": "task_started"}}, append=True)
                reader.scan_after = 0
                reader.index_codex()
                self.assertEqual(reads.call_count, 1)
                second = root / "archived" / "second.jsonl"
                second.parent.mkdir()
                second.write_text('{"type":')
                reader.scan_after = 0
                reader.index_codex()
                self.assertNotIn(second, reader.headers)
                write(second, codex_meta("second", name="Meitner"))
                reader.scan_after = 0
                reader.index_codex()
                self.assertEqual(len(reader.links["parent"]), 2)
                path.unlink()
                reader.scan_after = 0
                reader.index_codex()
                self.assertEqual(len(reader.links["parent"]), 1)

    def test_codex_parent_id_recovers_from_long_header_in_a_bounded_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "parent.jsonl"
            write(path, {"type": "session_meta", "timestamp": START, "payload": {
                "id": "parent", "base_instructions": "Synthetic instructions " * 2000}},
                *[{"type": "ignored", "padding": "x" * 1000} for _ in range(20)])
            write(root / "child.jsonl", codex_meta("child"))
            parent = Cursor(path)
            with patch("herdr_agent_grid.telemetry.MAX_READ", 1024):
                Telemetry.update(parent, "codex")
            self.assertEqual(parent.session_id, "")
            agent = Agent("w1:p1", "codex", "Parent", "Task", "Project", "working", session_kind="path", session_ref=str(path))
            reader = Subagents()
            with patch("herdr_agent_grid.subagents.roots", return_value=[root]):
                self.assertEqual(reader.read(agent, parent)[0].name, "Raman")
                self.assertTrue(parent.session_header_checked)
                self.assertEqual(parent.session_id, "parent")

    def test_telemetry_publishes_child_snapshot_without_mutating_earlier_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, parent, folder = self.claude_fixture(directory)
            agent = replace(agent, session_kind="path", session_ref=str(parent.path))
            path = folder / "agent-child.jsonl"
            write(path, claude_message())
            telemetry = Telemetry()
            with patch("herdr_agent_grid.telemetry.roots", return_value=[parent.path.parent]):
                first = telemetry.read(agent)
                write(path, claude_message("m2"), append=True)
                second = telemetry.read(agent)
            self.assertEqual(first.cost, second.cost)
            self.assertGreater(second.subagents[0].estimated_cost, first.subagents[0].estimated_cost)
            self.assertIsNot(first.subagents, second.subagents)

    def test_tile_and_details_show_simple_children_without_adding_to_overview(self):
        state = demo_state()
        parent = state.agents[0]
        children = (Subagent("one", "API tests", "claude-sonnet-4-6", "high", estimated_cost=.09, duration_s=81, status="working"),
                    Subagent("two", "Review", "gpt-6.1-sol", "xhigh", cost=.15, duration_s=45, status="done"))
        state.metrics[parent.pane_id].subagents = children
        view = View(icons="unicode")
        frame = plain_frame(view.draw(state, 140, 38), 140, 38)
        self.assertIn("API tests", frame)
        self.assertIn("subagent 1/2", frame)
        self.assertIn("Sonnet 4.6@high · ~$0.09 · 1m 21s", frame)
        first_total = view.overview(state)[1]
        state.metrics[parent.pane_id].subagents = (replace(children[0], cost=9999), children[1])
        self.assertEqual(view.overview(state)[1], first_total)
        view.zoom = True
        frame = plain_frame(view.draw(state, 140, 38), 140, 38)
        self.assertIn("SUBAGENTS  1 working / 2 total", frame)
        self.assertIn("MODEL@EFFORT", frame)
        self.assertIn("gpt-6.1-sol@xhigh", frame)
        self.assertIn("$0.15", frame)
        self.assertIn("45s", frame)
        self.assertIn("LATEST ASSISTANT MESSAGE", frame)

    def test_compact_cards_overflow_and_unicode_columns_remain_bounded(self):
        state = demo_state()
        for metric in state.metrics.values():
            metric.subagents = tuple(Subagent(str(i), "評審 é" * 15, "gpt-6.1-sol", "xhigh", cost=.02, duration_s=99) for i in range(20))
        view = View(icons="unicode")
        for width, height, zoom in ((80, 24, False), (140, 38, True), (50, 40, True), (20, 20, False)):
            view.zoom = zoom
            commands = view.draw(state, width, height)
            self.assertTrue(all(c.x + sum(cell_width(char) for char in c.text) <= width for c in commands))
            frame = plain_frame(commands, width, height)
            if width >= 80:
                self.assertIn("subagents" if not zoom else "SUBAGENTS", frame)
            if zoom:
                self.assertIn("PgUp/PgDn scroll", frame)
                self.assertIn("LATEST ASSISTANT MESSAGE", frame)
        row = child_row(("評審 é", "Sonnet@high", "$0.03", "99s"), 60)
        self.assertEqual(sum(cell_width(char) for char in row), 60)

    def test_large_parent_tiles_show_all_nine_children_and_working_count(self):
        state = demo_state()
        state.agents = state.agents[:2]
        children = tuple(Subagent(str(i), f"Worker {i}", "claude-opus-5-5", "high", cost=.03,
                                 duration_s=99, status="working" if i < 3 else "done") for i in range(9))
        for metric in state.metrics.values():
            metric.subagents = children
        for width, height in ((160, 70), (100, 70)):
            view = View(icons="unicode")
            commands = view.draw(state, width, height)
            frame = plain_frame(commands, width, height)
            self.assertEqual(frame.count("3 working / 9 total"), 3)  # Two cards and selected footer.
            for i in range(9):
                self.assertEqual(frame.count(f"Worker {i}"), 2)
            self.assertIn("Opus 5.5@high", frame)
            self.assertIn("$0.03", frame)
            self.assertIn("1m 39s", frame)

    def test_all_children_are_reachable_in_short_details_with_scroll_and_resize(self):
        state = demo_state()
        state.metrics[state.agents[0].pane_id].subagents = tuple(
            Subagent(str(i), f"Child {i:02}", "gpt-6.1-sol", "high", cost=.01, status="working") for i in range(20))
        view = View(icons="unicode")
        view.zoom = True
        seen = set()
        for _ in range(25):
            frame = plain_frame(view.draw(state, 80, 24), 80, 24)
            seen.update(i for i in range(20) if f"Child {i:02}" in frame)
            self.assertTrue(view.scroll_children(view.child_capacity))
        self.assertEqual(seen, set(range(20)))
        self.assertGreater(view.child_offset, 0)
        view.draw(state, 140, 80)
        self.assertEqual(view.child_offset, 0)  # All children now fit.
        self.assertTrue(view.scroll_children(-100))
        self.assertEqual(view.child_offset, 0)
        view.zoom = False
        self.assertFalse(view.scroll_children(1))

    def test_short_zoom_prioritizes_child_rows_and_stops_invisible_animation(self):
        state = demo_state()
        state.metrics[state.agents[0].pane_id].subagents = tuple(
            Subagent(str(i), f"Small child {i}", "gpt-6.1-sol", "high", cost=.03, duration_s=45, status="working") for i in range(9))
        view = View(icons="unicode")
        view.zoom = True
        seen = set()
        for _ in range(12):
            frame = plain_frame(view.draw(state, 80, 18), 80, 18)
            self.assertFalse(view.animating)
            self.assertIn("$0.03", frame)
            self.assertIn("45s", frame)
            seen.update(i for i in range(9) if f"Small child {i}" in frame)
            self.assertTrue(view.scroll_children(view.child_capacity))
        self.assertEqual(seen, set(range(9)))

    def test_background_shell_notifications_are_not_added_as_subagents(self):
        cursor = Cursor(Path("unused"))
        notice = "<task-notification><task-id>background-shell</task-id><tool-use-id>bash-call</tool-use-id><status>failed</status></task-notification>"
        consume(cursor, {"type": "queue-operation", "operation": "enqueue", "timestamp": END, "content": notice}, "claude")
        self.assertEqual(cursor.child_hints, {})


if __name__ == "__main__":
    unittest.main()
