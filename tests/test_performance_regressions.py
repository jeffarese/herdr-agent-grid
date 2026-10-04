"""Behavior protected by the performance work: ordering, snapshots and painting."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from herdr_agent_grid.app import paint
from herdr_agent_grid.demo import demo_state
from herdr_agent_grid.model import cell_width
from herdr_agent_grid.refresh import Refresher
from herdr_agent_grid.telemetry import Metrics
from herdr_agent_grid.view import Draw, View, plain_frame
import importlib.util
spec = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class Screen:
    def __init__(self, width, height):
        self.width, self.height, self.writes = width, height, 0
        self.erase()
    def erase(self): self.cells = [[" "] * self.width for _ in range(self.height)]
    def move(self, y, x): self.y, self.x = y, x
    def clrtoeol(self): self.cells[self.y][self.x:] = [" "] * (self.width - self.x)
    def addstr(self, y, x, text, style):
        self.writes += 1
        for c in text:
            size = cell_width(c)
            if size:
                self.cells[y][x] = c
                for offset in range(1, size): self.cells[y][x+offset] = ""
                x += size
            elif x: self.cells[y][x-1] += c
    def noutrefresh(self): pass
    def frame(self): return "\n".join("".join(row) for row in self.cells)

class PerformanceRegressions(unittest.TestCase):
    def test_active_total_is_prominent_and_independent_of_filters(self):
        state, view = demo_state(), View()
        frame = plain_frame(view.draw(state, 140, 38), 140, 38)
        self.assertIn("3 ACTIVE / 6 TOTAL", frame.splitlines()[0])
        view.query = "done"
        frame = plain_frame(view.draw(state, 140, 38), 140, 38)
        self.assertIn("3 ACTIVE / 6 TOTAL", frame.splitlines()[0])
        self.assertIn("/ 1 of 6", frame.splitlines()[0])
        state.agents = [replace(agent, status="done") for agent in state.agents]
        frame = plain_frame(view.draw(state, 80, 24), 80, 24)
        self.assertIn("0 ACTIVE / 6 TOTAL", frame.splitlines()[0])

    def test_selection_has_unique_title_bar_double_border_and_keeps_status_color(self):
        state, view = demo_state(), View()
        first = view.draw(state, 140, 38)
        self.assertEqual(sum(c.style.startswith("selection:") and "SELECTED" in c.text for c in first), 1)
        self.assertEqual(sum(c.text.startswith("╔") for c in first), 1)
        view.move(1)
        second = view.draw(state, 140, 38)
        selected = next(c for c in second if "SELECTED" in c.text)
        self.assertIn("grid", selected.text)
        self.assertEqual(selected.style, "selection:working")
        view.selected = next(a.pane_id for a in state.agents if a.status == "done")
        third = view.draw(state, 140, 38)
        selected = next(c for c in third if "SELECTED" in c.text)
        self.assertEqual(selected.style, "selection:done")
        self.assertEqual(sum(c.text.startswith("╔") for c in third), 1)

    def test_working_first_preserves_selection_and_stable_peer_order(self):
        state = demo_state()
        state.revision = 1
        view = View()
        view.arrange(state, 140, 38)
        working = [a.pane_id for a in state.agents if a.status == "working"]
        self.assertEqual([a.pane_id for a in view.items[:len(working)]], working)
        view.selected = working[-1]
        state.agents = [replace(a, status="done") if a.pane_id == working[0] else a for a in state.agents]
        state.revision += 1
        view.arrange(state, 140, 38)
        self.assertEqual(view.selected, working[-1])
        self.assertEqual(view.items[0].status, "working")
        view.query = "done"
        view.arrange(state, 80, 24)
        self.assertTrue(all(a.status == "done" for a in view.items))
        view.zoom = True
        view.arrange(state, 80, 24)
        self.assertEqual(len(view.visible), 1)

    def test_navigation_reuses_inventory_but_refresh_and_filter_invalidate_it(self):
        state = demo_state()
        state.revision = 1
        view = View()
        view.arrange(state, 140, 38)
        original = view.items
        for _ in range(3):
            view.move(1)
            view.arrange(state, 140, 38)
            self.assertIs(view.items, original)
            self.assertEqual(view.chosen.pane_id, view.selected)
        view.query = "done"
        view.arrange(state, 140, 38)
        self.assertIsNot(view.items, original)
        self.assertEqual(view.chosen.status, "done")
        state.agents = [replace(a, status="working") for a in state.agents]
        state.revision += 1
        view.arrange(state, 140, 38)
        self.assertEqual(view.items, [])

    def test_only_visible_cores_trigger_animation_ticks(self):
        state, view = demo_state(), View()
        view.arrange(state, 140, 38)
        self.assertTrue(view.animating)
        view.arrange(state, 80, 24)
        self.assertFalse(view.animating)
        view.zoom = True
        view.arrange(state, 80, 24)
        self.assertTrue(view.animating)
        view.motion = False
        self.assertFalse(view.animating)
        view.motion = True
        state.agents = [replace(a, status="done") for a in state.agents]
        view.arrange(state, 140, 38)
        self.assertFalse(view.animating)

    def test_overview_refreshes_costs_status_and_shared_session_coverage(self):
        state = demo_state()
        state.revision = 1
        view = View()
        first = view.overview(state)
        self.assertIs(view.overview(state), first)
        a, b = state.agents[:2]
        state.agents[:2] = [replace(a, provider="claude", session_kind="id", session_ref="shared"),
                           replace(b, provider="claude", session_kind="id", session_ref="shared")]
        state.metrics[a.pane_id].cost = 0
        state.metrics[b.pane_id].cost = 1000
        state.revision += 1
        counts, summary = view.overview(state)
        self.assertEqual(counts["working"], 3)
        self.assertIn("(4/5 covered)", summary)
        self.assertNotIn("1000", summary)
        self.assertIsNot(view.overview(state), first)
        # Unversioned fixture mutation must never reuse stale aggregates.
        state.revision = 0
        state.agents = [replace(a, status="done") for a in state.agents]
        self.assertEqual(view.overview(state)[0]["done"], 6)
        state.agents.pop()
        self.assertEqual(view.overview(state)[0]["done"], 5)

    def test_fast_terminal_read_publishes_while_another_pane_is_slow(self):
        release, fast_read = threading.Event(), threading.Event()
        class Client:
            def snapshot(self):
                return {"agents": [{"pane_id": "slow", "agent": "codex"}, {"pane_id": "fast", "agent": "codex"}]}
            def read(self, pid, lines):
                if pid == "slow":
                    release.wait(2)
                else:
                    fast_read.set()
                return "Tool: " + pid
        worker = Refresher(Client(), interval=.05)
        worker.telemetry.read = lambda a: Metrics()
        worker.request({"slow": 40, "fast": 40})
        worker.start()
        try:
            self.assertTrue(fast_read.wait(1))
            deadline = time.monotonic() + 1
            while "fast" not in worker.get().previews and time.monotonic() < deadline:
                time.sleep(.01)
            state = worker.get()
            self.assertEqual(state.previews.get("fast"), "Tool: fast")
            self.assertNotIn("slow", state.previews)
        finally:
            release.set()
            worker.close()
            worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())

    def test_snapshot_reuses_unchanged_state_and_isolates_metrics(self):
        refresh = Refresher(None)
        refresh.state = demo_state()
        refresh.state.revision = 1
        first = refresh.get()
        self.assertIs(refresh.get(first), first)
        pid = first.agents[0].pane_id
        refresh.state.metrics[pid].last_message = "New update"
        refresh.state.revision += 1
        second = refresh.get(first)
        self.assertIsNot(second, first)
        self.assertNotEqual(first.metrics[pid].last_message, second.metrics[pid].last_message)

    @patch("herdr_agent_grid.app.curses.doupdate")
    def test_row_diff_matches_full_frame_and_clears_removed_content(self, update):
        width, height = 140, 38
        screen, view, state = Screen(width, height), View(icons="unicode"), demo_state()
        commands = view.draw(state, width, height, animation_time=1.3)
        previous = paint(screen, commands, {}, width, height)
        self.assertEqual(screen.frame(), plain_frame(commands, width, height))
        writes = screen.writes
        paint(screen, commands, {}, width, height, previous)
        self.assertEqual(screen.writes, writes)
        view.query = "done"
        commands = view.draw(state, width, height, animation_time=1.4)
        paint(screen, commands, {}, width, height, previous)
        self.assertEqual(screen.frame(), plain_frame(commands, width, height))

    @patch("herdr_agent_grid.app.curses.doupdate")
    def test_resize_repaints_equal_rows_and_preserves_wide_glyphs(self, update):
        screen = Screen(20, 4)
        commands = [Draw(0, 0, "界é"), Draw(4, 0, "abc"), Draw(5, 0, "!")]
        previous = paint(screen, commands, {}, 20, 4)
        screen.height = 5
        paint(screen, commands, {}, 20, 5, previous)
        self.assertEqual(screen.frame(), plain_frame(commands, 20, 5))

    def test_rename_migrates_custom_shortcuts_without_duplicates(self):
        before = "# custom binding\n[[keys.command]]\nkey = 'cmd+shift+g'\ntype = 'plugin_action'\ncommand = 'herdr-grid.open' # retained\ndescription = 'mine'\n"
        after = installer.config_with_shortcut(before)
        self.assertIn("key = 'cmd+shift+g'", after)
        self.assertIn("command = 'herdr-agent-grid.open' # retained", after)
        self.assertEqual(installer.config_with_shortcut(after), after)
        self.assertEqual(after.count("[[keys.command]]"), 1)

    def test_matched_transcript_avoids_redundant_terminal_read(self):
        gate = threading.Event()
        class Client:
            def snapshot(self): return {"agents": [{"pane_id": "w1:p1", "agent": "claude"}]}
            def read(self, *args): raise AssertionError("Unnecessary terminal read")
        refresh = Refresher(Client(), interval=.05)
        def read(agent):
            gate.set()
            return Metrics(last_call="Edit", call_source="transcript")
        refresh.telemetry.read = read
        refresh.request({"w1:p1": 40})
        refresh.start()
        try:
            self.assertTrue(gate.wait(2))
        finally:
            refresh.close()
            refresh.thread.join(2)
        self.assertFalse(refresh.thread.is_alive())
        self.assertEqual(refresh.get().previews, {})

if __name__ == "__main__": unittest.main()
