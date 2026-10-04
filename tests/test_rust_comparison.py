"""Cross-runtime behavior gates; no timing thresholds in CI."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compare_rust", ROOT / "benchmarks/compare_rust.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


@unittest.skipUnless(compare.RUST.exists(), "build the release Rust prototype to run comparison gates")
class RustComparisonTests(unittest.TestCase):
    def test_frames_and_incremental_transcripts_match_current_python(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            fixtures = {n: compare.fixture(directory, n) for n in (6, 24, 100, 1000)}
            result = compare.validate(directory, fixtures, compare.transcript(directory))
            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["draw_command_frames"], 176)

    def test_real_terminals_paint_equivalent_navigation_and_filter_results(self):
        with tempfile.TemporaryDirectory() as folder:
            path = compare.fixture(Path(folder), 6)
            keys = [b"\x1bOC", b"\x1bOD", b"\t", b"z", b"z", b"\x1bOD",
                    b"/", b"g", b"r", b"i", b"d", b"\x7f", b"\x7f", b"\x7f", b"\x7f", b"\r"]
            runs = {runtime: compare.terminal_run(compare.command(runtime, path, "--mode", "pty"), 1, keys)
                    for runtime in ("python", "rust")}
            compare.close_enough(runs["python"]["key_states"], runs["rust"]["key_states"])
            for result in runs.values():
                self.assertEqual(result["key_to_paint_ms"]["samples"], len(keys))
                self.assertGreater(result["peak_rss_bytes"], 0)

    def test_background_replay_keeps_navigation_semantics(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = compare.fixture(directory, 6)
            log = compare.transcript(directory, messages=200)
            keys = [b"\x1bOC", b"\x1bOD", b"z", b"z"]
            runs = {runtime: compare.terminal_run(compare.command(runtime, path, "--mode", "pty",
                    "--background-transcript", log), 1, keys) for runtime in ("python", "rust")}
            compare.close_enough(runs["python"]["key_states"], runs["rust"]["key_states"])


if __name__ == "__main__":
    unittest.main()
