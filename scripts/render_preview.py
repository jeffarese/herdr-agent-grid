#!/usr/bin/env python3
"""Quick shared-renderer PNG/GIF export; use render_demo.py for the full film."""
from PIL import Image
from render_demo import ROOT, render
out = ROOT / "docs/media"
out.mkdir(parents=True, exist_ok=True)
render(3).save(out / "poster.png")
frames = [render(3 + i / 10).resize((1120, 630), Image.Resampling.LANCZOS) for i in range(24)]
frames[0].save(out / "preview.gif", save_all=True, append_images=frames[1:],
               duration=100, loop=0, optimize=True, disposal=2)
