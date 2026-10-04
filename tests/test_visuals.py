from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from herdr_agent_grid.icons import LOGOS, logo_for
from herdr_agent_grid.model import cell_width, clean
from herdr_agent_grid.telemetry import Cursor, Metrics, consume
from herdr_agent_grid.visuals import core, core_runs, phase_for


class VisualTests(unittest.TestCase):
    def test_icons_use_font_when_available_and_have_portable_fallbacks(self):
        with patch("herdr_agent_grid.icons.font_path", return_value=None):
            self.assertEqual(logo_for("claude"), "✻")
            self.assertEqual(logo_for("codex", "ascii"), "X")
            self.assertEqual(logo_for("gemini", "font"), LOGOS["gemini"])
        with patch("herdr_agent_grid.icons.font_path", return_value=Path("font.ttf")):
            self.assertEqual(logo_for("claude code"), LOGOS["claude"])
        self.assertEqual(clean(LOGOS["claude"] + "\x00"), LOGOS["claude"])

    def test_lifecycle_wins_over_stale_transcript_phase(self):
        metrics = Metrics(phase="thinking")
        self.assertEqual(phase_for("working", metrics), "thinking")
        self.assertEqual(phase_for("done", metrics), "done")
        self.assertEqual(phase_for("blocked", metrics), "blocked")
        self.assertEqual(phase_for("idle", metrics), "idle")

    def test_animation_stays_in_bounds_and_settled_or_reduced_motion_is_still(self):
        for phase in ("working", "thinking", "writing", "tool", "done", "idle", "blocked", "unknown"):
            for width, rows in ((1, 1), (15, 1), (42, 2)):
                with self.subTest(phase=phase, width=width, rows=rows):
                    frame = core(phase, width, rows, 1.0, "pane")
                    self.assertTrue(all(0 <= x < width and 0 <= y < rows and cell_width(char) == 1
                                        for x, y, char, _ in frame))
                    if phase in ("done", "idle", "blocked", "unknown"):
                        self.assertEqual(frame, core(phase, width, rows, 9.0, "pane"))
                    self.assertEqual(core(phase, width, rows, 1.0, "pane", motion=False),
                                     core(phase, width, rows, 9.0, "pane", motion=False))

    def test_batched_animation_preserves_every_cell_and_style(self):
        for phase in ("working", "thinking", "writing", "tool", "done", "idle", "blocked", "unknown"):
            for width, rows in ((1, 1), (42, 2), (134, 2)):
                for tick in (0, 1.3, 11.2):
                    for motion in (True, False):
                        expected = {(x, y): (char, style) for x, y, char, style in core(phase, width, rows, tick, "pane", motion)}
                        runs = core_runs(phase, width, rows, tick, "pane", motion)
                        actual = {(x+i, y): (char, style) for x, y, text, style in runs for i, char in enumerate(text)}
                        self.assertEqual(actual, expected, (phase, width, rows, tick, motion))

    def test_tool_trail_deduplicates_streaming_blocks_and_marks_confirmed_errors(self):
        cursor = Cursor(Path("unused"))
        call = {"type": "assistant", "timestamp": "2026-10-04T12:00:00Z", "message": {
            "content": [{"type": "tool_use", "id": "call1", "name": "Bash"}]}}
        consume(cursor, call, "claude")
        consume(cursor, call, "claude")
        self.assertEqual(len(cursor.metrics.trail), 1)
        self.assertEqual(cursor.metrics.phase, "tool")
        consume(cursor, {"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "call1", "is_error": True}]}}, "claude")
        self.assertTrue(cursor.metrics.trail[0].done)
        self.assertTrue(cursor.metrics.trail[0].error)
        consume(cursor, {"type": "assistant", "message": {"content": [{
            "type": "thinking", "thinking": "private text"}]}}, "claude")
        self.assertEqual(cursor.metrics.phase, "thinking")
        self.assertNotIn("private", repr(cursor.metrics))


if __name__ == "__main__":
    unittest.main()
