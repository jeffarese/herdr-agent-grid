#!/usr/bin/env python3
"""Create a reproducible source release without publishing anything."""
import gzip
import hashlib
import io
from pathlib import Path
import sys
import tarfile
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from herdr_agent_grid import __version__
name = "herdr-agent-grid-" + __version__
out = ROOT / "dist"
out.mkdir(exist_ok=True)
archive = out / (name + ".tar.gz")
with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as tar:
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or any(part in {".git", ".venv", "__pycache__", "dist", ".agents", ".codex"} for part in rel.parts): continue
        if p.name == ".DS_Store" or p.suffix in {".pyc", ".pyo"}: continue
        data = p.read_bytes()
        info = tarfile.TarInfo(name + "/" + rel.as_posix())
        info.size, info.mtime, info.mode = len(data), 0, 0o644
        tar.addfile(info, io.BytesIO(data))
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
(archive.with_suffix(archive.suffix + ".sha256")).write_text(checksum + "  " + archive.name + "\n")
print(archive)
print(checksum)
