#!/usr/bin/env python3
"""Render actual TUI commands with scripted synthetic data; export a launch film.

Development dependencies: Pillow, ffmpeg. Runtime remains standard-library only.
No live Herdr socket, session logs, API, or real messages are accessed.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from functools import lru_cache
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from herdr_agent_grid.demo import demo_state
from herdr_agent_grid.icons import font_path
from herdr_agent_grid.model import cell_width
from herdr_agent_grid.theme import COLORS
from herdr_agent_grid.telemetry import Subagent
from herdr_agent_grid.view import View

SIZE = (1920, 1080)
COLS, ROWS, CELL, LINE = 140, 38, 12, 21
PANEL = (96, 138, 1824, 1000)
CONTENT = (120, 186)
BG, PANEL_BG = "#0b0e18", "#151925"
DURATION, FPS = 30, 20


def font(size, bold=False, mono=False):
    candidates = (["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"] if mono else
                  ["/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    path = next((p for p in candidates if Path(p).exists()), None)
    if path is None: raise RuntimeError("Install a readable TrueType font for media export")
    bold_path = path.replace("Arial.ttf", "Arial Bold.ttf").replace("Sans.ttf", "Sans-Bold.ttf")
    if bold and Path(bold_path).exists(): path = bold_path
    return ImageFont.truetype(path, size, index=1 if bold and path.endswith("Menlo.ttc") else 0)

MONO, BOLD = font(18, mono=True), font(18, bold=True, mono=True)
TITLE, CAPTION, SMALL = font(44, bold=True), font(25), font(17)
ICON = ImageFont.truetype(str(font_path()), 24) if font_path() else None


def background():
    im = Image.new("RGB", SIZE, BG)
    glow = Image.new("RGB", (480, 270), BG)
    d = ImageDraw.Draw(glow)
    d.ellipse((-160, -160, 390, 210), fill="#241a45")
    d.ellipse((240, 60, 560, 380), fill="#0d2731")
    glow = glow.filter(ImageFilter.GaussianBlur(65)).resize(SIZE, Image.Resampling.BILINEAR)
    im.paste(glow)
    d = ImageDraw.Draw(im)
    d.text((100, 37), "herdr-agent-grid", font=TITLE, fill="#f0f2f9")
    d.rounded_rectangle((1516, 51, 1818, 87), radius=18, fill="#25263b", outline="#39334f")
    d.text((1534, 59), "SYNTHETIC DATA  /  DEMO", font=SMALL, fill="#bcaedc")
    # Terminal chrome and restrained shadow, outside the actual TUI.
    d.rounded_rectangle((89, 146, 1834, 1010), radius=18, fill="#060914")
    d.rounded_rectangle(PANEL, radius=15, fill=PANEL_BG, outline="#373b50", width=1)
    for x, color in ((123, "#fb7185"), (145, "#fbbf24"), (167, "#34d399")):
        d.ellipse((x, 155, x+10, 165), fill=color)
    d.text((775, 149), "HERDR  /  AGENT GRID", font=SMALL, fill="#9da5ba")
    return im

BASE = background()


@lru_cache(maxsize=4096)
def glyph(char, style):
    im = Image.new("RGBA", (CELL * max(1, cell_width(char)), LINE))
    d = ImageDraw.Draw(im)
    color = PANEL_BG if style.startswith("selection:") else COLORS.get(style, COLORS["normal"])
    if 0x2800 <= ord(char) <= 0x28ff:
        bits = ord(char) - 0x2800
        for dx, dy, bit in ((0,0,1),(0,1,2),(0,2,4),(0,3,64),(1,0,8),(1,1,16),(1,2,32),(1,3,128)):
            if bits & bit:
                x, y = 2+dx*6, 3+dy*4
                d.ellipse((x,y,x+1,y+1), fill=color)
    elif 0xe1a0 <= ord(char) <= 0xe1ba and ICON:
        box = ICON.getbbox(char)
        mark = Image.new("RGBA", (box[2]-box[0]+4, box[3]-box[1]+4))
        ImageDraw.Draw(mark).text((2-box[0],2-box[1]), char, font=ICON, fill=color)
        mark.thumbnail((CELL, LINE-1), Image.Resampling.LANCZOS)
        im.paste(mark, ((CELL-mark.width)//2,(LINE-mark.height)//2), mark)
    else:
        strong = style in ("title","metric","brand","selected") or style.startswith(("harness:", "selection:"))
        d.text((0,0), char, font=BOLD if strong else MONO, fill=color)
    return im


def synthetic(t):
    state = demo_state()
    # demo_state contains only authored fixtures. All values below are scripted.
    state.updated = time.monotonic()
    state.revision = 1 + int(t*10)
    metrics = state.metrics
    if t >= 10:
        now = time.time()
        names = ("API review", "UI audit", "Cache checks", "Data audit", "Error paths", "Copy review", "A11y checks")
        extra = tuple(Subagent(f"demo-extra-{i}", name, "gpt-6.1-sol", "high" if i % 2 else "xhigh",
            estimated_cost=.02 + i * .01, started_at=now - 40 - i * 9,
            duration_s=None if i < 2 else 28 + i * 5, status="working" if i < 2 else "done")
            for i, name in enumerate(names))
        base = metrics["w2:p1"].subagents
        metrics["w2:p1"].subagents = (base[0], *extra[:2], base[1], *extra[2:])
    models = ["claude-sonnet-5-5", "gpt-6.1-sol", "claude-opus-5-5", "gpt-6.1-sol", "gemini", "claude-opus-5-5"]
    for i, a in enumerate(state.agents):
        m = metrics[a.pane_id]
        m.model = models[i]
        m.source = "Synthetic session log" if i != 4 else "Herdr status · synthetic"
        if m.started_at: m.started_at -= t
        if m.call_at: m.call_at -= t
        if m.message_at: m.message_at -= t
        m.subagents = tuple(replace(child,
            started_at=child.started_at - t if child.started_at else None,
            finished_at=child.finished_at - t if child.finished_at else None)
            for child in m.subagents)
    if t >= 4:
        m = metrics["w2:p1"]
        m.last_call, m.call_done, m.phase = "apply_patch", True, "writing"
        m.last_message = "Keyboard navigation is verified. I’m polishing the focus states."
        m.tokens, m.estimated_cost = 96720, .46
    if t >= 8:
        m = metrics["w1:p1"]
        m.last_message = "The responsive layout is ready. All breakpoints look good."
        m.tokens, m.cost = 190400, 1.31
    if t >= 22:
        state.agents = [replace(a, status="done") if a.pane_id in ("w1:p1", "w2:p1") else a for a in state.agents]
        metrics["w2:p1"].last_message = "Everything passes. The agent grid is ready for review."
        for pane in ("w1:p1", "w2:p1"):
            metrics[pane].subagents = tuple(replace(child,
                status="done",
                duration_s=child.duration_s if child.duration_s is not None else time.time() - child.started_at - (t - 22))
                for child in metrics[pane].subagents)
    view = View(icons="auto")
    if 6 <= t < 10: view.selected = "w2:p1"
    elif 10 <= t < 16:
        view.selected, view.zoom = "w2:p1", True
    elif 16 <= t < 21:
        view.searching = t < 18
        view.query = "working"[:min(7, max(1, int((t-16)*4)+1))]
    elif t >= 21:
        view.selected = "w6:p1"
    return state, view


def caption(t):
    if t < 6: return "One panel. Every agent. Working agents first.", "Cmd + G"
    if t < 10: return "Harness icons. Model@Effort. The latest message at a glance.", "Tab / arrows"
    if t < 16: return "See subagents: name, Model@Effort, API cost and time.", "z  details"
    if t < 21: return "Find the agent you need by status, workspace, task or tool.", "/  filter"
    if t < 26: return "Working in orange. Completed in green. Focus stays with your agent.", "Enter  jump to agent"
    return "Your agents. One command center.", "herdr-agent-grid"


def render(t):
    im = BASE.copy()
    d = ImageDraw.Draw(im)
    state, view = synthetic(t)
    for cmd in view.draw(state, COLS, ROWS, animation_time=t+1.3):
        if not cmd.text: continue
        x, y = CONTENT[0]+cmd.x*CELL, CONTENT[1]+cmd.y*LINE
        span = sum(cell_width(c) for c in cmd.text)*CELL
        bg = COLORS.get(cmd.style.split(":", 1)[1], COLORS["selected"]) if cmd.style.startswith("selection:") else PANEL_BG
        d.rectangle((x,y,x+max(1,span)-1,y+LINE-1), fill=bg)
        for c in cmd.text:
            g = glyph(c, cmd.style)
            im.paste(g,(x,y),g)
            x += cell_width(c)*CELL
    text, key = caption(t)
    d.text((100, 1026), text, font=CAPTION, fill="#dde2ef")
    key_width = d.textlength(key, font=SMALL)
    d.rounded_rectangle((1818-key_width-32,1020,1824,1058), radius=9, fill="#252b3d", outline="#3d445d")
    d.text((1802-key_width,1030),key,font=SMALL,fill="#c4b5fd")
    # Subtle timed progress bar; no fictitious terminal throughput indicators.
    d.rounded_rectangle((100, 117, 1820, 120), radius=2, fill="#2e3045")
    d.rounded_rectangle((100,117,100+max(3,1720*t/DURATION),120), radius=2, fill="#b39dff")
    return im


def hero():
    im = Image.new("RGB", (1600,480), BG)
    d = ImageDraw.Draw(im)
    for i, color in enumerate(("#fb923c","#b39dff","#34d399","#5fe3f7")):
        x,y = 106+(i%2)*54,173+(i//2)*54
        d.rounded_rectangle((x,y,x+41,y+41),radius=9,fill="#171d2c",outline=color,width=2)
        d.ellipse((x+16,y+16,x+24,y+24),fill=color)
    d.text((266, 141), "herdr-agent-grid", font=font(80,bold=True), fill="#f0f2f9")
    d.text((270, 249), "Your agents. One command center.", font=font(35), fill="#c0c5d8")
    d.text((270, 323), "LIVE STATUS    /    LATEST TOOLS & MESSAGES    /    API COST",font=font(17,mono=True),fill="#9da5ba")
    return im


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stills", action="store_true", help="export poster, hero and contact sheet only")
    args = parser.parse_args()
    out = ROOT / "docs/media"
    out.mkdir(parents=True,exist_ok=True)
    hero().save(out / "hero.png")
    render(3).save(out / "poster.png")
    render(12).save(out / "subagents.png")
    frames = [render(t).resize((640,360),Image.Resampling.LANCZOS) for t in (3,8,12,17,23,28)]
    contact = Image.new("RGB",(1280,1080),BG)
    for i,frame in enumerate(frames): contact.paste(frame,((i%2)*640,(i//2)*360))
    contact.save(out / "contact-sheet.png")
    if args.stills: return
    if not shutil.which("ffmpeg"): raise RuntimeError("ffmpeg is needed to export the video")
    command = ["ffmpeg","-hide_banner","-loglevel","error","-y","-f","rawvideo","-pix_fmt","rgb24",
               "-s","1920x1080","-r",str(FPS),"-i","-","-an","-c:v","libx264","-preset","medium",
               "-crf","21","-pix_fmt","yuv420p","-movflags","+faststart",str(out/"demo.mp4")]
    process = subprocess.Popen(command,stdin=subprocess.PIPE)
    try:
        for i in range(DURATION*FPS):
            # UI motion has 10fps cadence, matching the actual curses app.
            process.stdin.write(render(int(i/FPS*10)/10).tobytes())
            if i % (FPS*5) == 0: print(f"Rendered {i//FPS}/{DURATION}s",flush=True)
    finally: process.stdin.close()
    if process.wait(): raise RuntimeError("Video encoder failed")
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(out/"demo.mp4"),
                    "-t","9","-vf","fps=8,scale=1120:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                    "-loop","0",str(out/"preview.gif")],check=True)
    print(f"Exported {out / 'demo.mp4'}",flush=True)

if __name__ == "__main__": main()
