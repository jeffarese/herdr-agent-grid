from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from herdr_agent_grid.client import Client, HerdrError
from herdr_agent_grid.demo import demo_state
from herdr_agent_grid.model import agents_from, cell_width, clean, clip, layout, wrap_cells
from herdr_agent_grid.refresh import Refresher
from herdr_agent_grid.view import View, plain_frame

spec = importlib.util.spec_from_file_location("grid_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class GridTests(unittest.TestCase):
    def test_layout_stays_in_bounds_and_tiles_do_not_overlap(self):
        for width, height in [(1, 1), (20, 5), (80, 20), (160, 44), (240, 64)]:
            for count in [0, 1, 2, 3, 6, 13, 100]:
                with self.subTest(width=width, height=height, count=count):
                    grid = layout(width, height, count)
                    occupied = set()
                    for rect in grid.rects:
                        self.assertGreater(rect.width, 0)
                        self.assertGreater(rect.height, 0)
                        cells = {(x, y) for x in range(rect.x, rect.x + rect.width)
                                 for y in range(rect.y, rect.y + rect.height)}
                        self.assertTrue(all(0 <= x < width and 0 <= y < height for x, y in cells))
                        self.assertFalse(cells & occupied)
                        occupied |= cells

    def test_inventory_uses_stable_ids_and_handles_null_metadata(self):
        snapshot = {"workspaces": [{"workspace_id": "w1", "label": "project"}],
                    "tabs": [{"tab_id": "w1:t1", "label": "task"}],
                    "agents": [{"pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1",
                                "agent": "codex", "name": None, "agent_status": "idle"},
                               {"pane_id": "w1:p1", "agent": "codex"},
                               {"pane_id": "w1:p2", "agent": None}]}
        agents = agents_from(snapshot)
        self.assertEqual(len(agents), 1)
        self.assertEqual((agents[0].pane_id, agents[0].title, agents[0].workspace),
                         ("w1:p1", "task", "project"))

    def test_selection_survives_reorder_and_disappearance(self):
        state = demo_state()
        view = View()
        view.arrange(state, 160, 44)
        view.move(3)
        selected = view.selected
        state.agents.reverse()
        view.arrange(state, 160, 44)
        self.assertEqual(view.selected, selected)
        state.agents = [a for a in state.agents if a.pane_id != selected]
        view.arrange(state, 160, 44)
        self.assertIn(view.selected, [a.pane_id for a in state.agents])

    def test_pagination_exposes_every_agent_and_zoom_preserves_selection(self):
        state = demo_state()
        state.agents = [replace(state.agents[0], pane_id=f"w1:p{i}") for i in range(35)]
        view = View()
        view.arrange(state, 80, 24)
        seen = set()
        while True:
            seen.update(a.pane_id for a in view.visible)
            if view.page == view.page_count - 1:
                break
            view.move(view.geometry.capacity)
            view.arrange(state, 80, 24)
        self.assertEqual(seen, {a.pane_id for a in state.agents})
        chosen = view.selected
        view.zoom = True
        view.arrange(state, 80, 24)
        self.assertEqual([a.pane_id for a in view.visible], [chosen])
        self.assertEqual(len(view.targets()), 1)

    def test_filter_and_mouse_hit_use_the_same_visible_tiles(self):
        state = demo_state()
        view = View()
        view.query = "blocked"
        view.arrange(state, 160, 44)
        self.assertEqual(len(view.items), 1)
        rect = view.geometry.rects[0]
        self.assertEqual(view.hit(rect.x + 2, rect.y + view.top + 1), view.chosen)
        self.assertIsNone(view.hit(0, 0))
        view.query = "no-such-agent"
        view.arrange(state, 160, 44)
        self.assertIsNone(view.chosen)
        self.assertEqual(view.targets(), {})

    def test_controls_are_stripped_and_unicode_does_not_escape_tile(self):
        self.assertEqual(clean("\x1b[31mred\x1b[0m\x1b]52;c;secret\x07\x00"), "red")
        self.assertEqual(clip("界界x", 3, True), "界…")
        self.assertEqual(clip("e\u0301界", 2), "e\u0301")
        state = demo_state()
        state.previews[state.agents[0].pane_id] = "界" * 100 + "\n" + "e\u0301" * 100
        for width, height in [(12, 8), (80, 24), (140, 38), (160, 44)]:
            view = View()
            frame = plain_frame(view.draw(state, width, height), width, height)
            self.assertEqual(len(frame.splitlines()), height)
            for line in frame.splitlines():
                self.assertEqual(sum(cell_width(c) for c in line), width)

    def test_message_wrap_preserves_wide_and_combining_text(self):
        for text in ("漢字テスト" * 10, "é" * 100, "first second third", "abcdefghi"):
            for width in (2, 7, 12, 30):
                lines = wrap_cells(text, width)
                self.assertTrue(all(sum(cell_width(c) for c in line) <= width for line in lines))
                self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
        self.assertEqual(wrap_cells("\x1b[31mred\x1b[0m text", 4), ["red", "text"])
        self.assertEqual(wrap_cells("界", 1), ["…"])

    def test_details_show_tool_target_and_wrap_latest_message(self):
        state, view = demo_state(), View()
        view.selected, view.zoom = state.agents[0].pane_id, True
        state.metrics[view.selected].last_message = "漢字テスト" * 20
        frame = plain_frame(view.draw(state, 80, 44), 80, 44)
        self.assertIn("Tool target      src/styles/dashboard.css", frame)
        body = frame.split("LATEST ASSISTANT MESSAGE", 1)[1]
        self.assertEqual(body.count("漢"), 20)
        self.assertEqual(body.count("字"), 20)

    def test_installer_preserves_config_and_is_idempotent(self):
        before = 'onboarding = false\n[keys]\nnext_tab = "ctrl+tab"\n[ui]\nsidebar_start_collapsed = false\n'
        after = installer.config_with_shortcut(before)
        self.assertTrue(after.startswith(before.rstrip()))
        self.assertEqual(installer.config_with_shortcut(after), after)
        self.assertIn('"prefix+a"', after)
        with self.assertRaisesRegex(ValueError, "already"):
            installer.config_with_shortcut('[keys]\ngoto = "cmd+g"\n')
        with self.assertRaisesRegex(ValueError, "already"):
            installer.config_with_shortcut('[[keys.command]]\nkey = ["ctrl+alt+g"]\ncommand = "other"\n')

    def test_cli_transport_and_server_errors(self):
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": ""}):
            client = Client()
        response = type("Process", (), {"returncode": 0, "stdout": json.dumps(
            {"result": {"snapshot": {"agents": []}}}).encode()})()
        with patch("subprocess.run", return_value=response):
            self.assertEqual(client.snapshot(), {"agents": []})
        failure = type("Process", (), {"returncode": 1, "stderr": b'{"error":{"message":"gone"}}'})()
        with patch("subprocess.run", return_value=failure):
            with self.assertRaisesRegex(HerdrError, "gone"):
                client.focus("w1:p1")

    def test_ambiguous_focus_transport_failure_is_not_retried(self):
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/no/socket"}):
            client = Client()
        with patch("socket.socket") as socket_mock, patch("subprocess.run") as cli:
            socket_mock.return_value.__enter__.return_value.connect.side_effect = OSError("disconnected")
            with self.assertRaisesRegex(HerdrError, "disconnected"):
                client.focus("w1:p1")
            cli.assert_not_called()

    def test_refresh_retains_failed_preview_and_clears_disappeared_agents(self):
        gate = threading.Event()
        class Fake:
            failed = False
            closed = False
            def snapshot(self):
                return {"agents": [] if self.closed else [{"pane_id": "w1:p1", "agent": "codex"}]}
            def read(self, pid, lines):
                gate.set()
                if self.failed:
                    raise HerdrError("preview failed")
                return "last output"
        client = Fake()
        worker = Refresher(client, interval=0.05)
        worker.request({"w1:p1": 5})
        worker.start()
        try:
            def wait_for(predicate):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if predicate(worker.get()):
                        return
                    time.sleep(0.01)
                self.fail("refresh did not publish expected state")
            wait_for(lambda s: s.previews.get("w1:p1") == "last output")
            client.failed = True
            worker.request({"w1:p1": 5}, force=True)
            wait_for(lambda s: "w1:p1" in s.errors)
            self.assertEqual(worker.get().previews["w1:p1"], "last output")
            client.closed = True
            worker.request({}, force=True)
            wait_for(lambda s: not s.agents)
            self.assertEqual(worker.get().previews, {})
        finally:
            worker.close()
            worker.thread.join(2)
            self.assertFalse(worker.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
