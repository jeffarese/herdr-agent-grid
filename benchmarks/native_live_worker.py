"""Python 1.1.1 reference with only a post-paint timing observer added."""
import curses
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from herdr_agent_grid import app
from herdr_agent_grid.client import Client
from rust_compare_worker import CountingScreen

def run(screen):
    counted = CountingScreen(screen)
    def painted(view, state):
        sys.stdout.write("\x1b]777;" + json.dumps(dict(input=counted.inputs, selected=view.selected,
                         query=view.query, zoom=view.zoom, revision=state.revision,
                         ready=bool(state.agents) and len(state.metrics)==len(state.agents))) + "\x07")
        sys.stdout.flush()
    app.run(counted, Client(), frame_observer=painted)
curses.wrapper(run)
