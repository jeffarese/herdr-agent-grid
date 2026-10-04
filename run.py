#!/usr/bin/env python3
"""Run from the plugin root without installation or third-party dependencies."""
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

def launch():
    try:
        from herdr_agent_grid.cli import main
        result = main()
    except Exception:
        traceback.print_exc()
        result = 1
    # Herdr closes the popup as soon as its process exits. Keep a failed
    # interactive launch visible so a startup error cannot disappear in a flash.
    diagnostics = {"--doctor", "--list", "--render", "--version", "--help", "-h"}
    if result and not diagnostics.intersection(sys.argv[1:]) and sys.stdin.isatty() and sys.stdout.isatty():
        print(f"\nAgent Grid could not start (Python {sys.version.split()[0]}).", flush=True)
        try:
            input("Press Enter to close. ")
        except (EOFError, KeyboardInterrupt):
            pass
    return result

if __name__ == "__main__":
    raise SystemExit(launch())
