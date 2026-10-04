#!/usr/bin/env python3
"""Build deterministic native/source archives from explicit release inputs."""
import argparse
import gzip
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import tomllib
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target")
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/herdr-agent-grid")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
    assert tomllib.loads((ROOT / "herdr-plugin.toml").read_text())["version"] == version
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"herdr-agent-grid-v{version}-{args.target or 'source'}.tar.gz"
    archive = args.output / name
    files = []
    if args.target:
        files.append(("bin/herdr-agent-grid", args.binary.read_bytes(), 0o755))
        for filename in ("herdr-plugin.toml", "run.sh", "install.sh", "README.md", "LICENSE", "CHANGELOG.md"):
            data = (ROOT / filename).read_bytes()
            if filename == "README.md":
                text = data.decode()
                raw = f"https://raw.githubusercontent.com/jeffarese/herdr-agent-grid/v{version}/"
                page = f"https://github.com/jeffarese/herdr-agent-grid/blob/v{version}/"
                text = re.sub(r'(!\[[^\]]*\]\()((?:docs|benchmarks)/[^)]+)(\))', lambda m:m[1]+raw+m[2]+m[3], text)
                text = text.replace('src="docs/', 'src="'+raw+'docs/')
                text = text.replace('](docs/', ']('+page+'docs/').replace('](benchmarks/', ']('+page+'benchmarks/')
                text = text.replace('href="docs/', 'href="'+page+'docs/').replace('href="benchmarks/', 'href="'+page+'benchmarks/')
                data = text.encode()
            files.append((filename, data, 0o755 if filename.endswith(".sh") else 0o644))
        cargo = os.environ.get("CARGO", "cargo")
        metadata = json.loads(subprocess.check_output([cargo, "metadata", "--locked", "--format-version", "1"], cwd=ROOT))
        notices = ["Third-party software bundled in Herdr Agent Grid\n"]
        for package in sorted(metadata["packages"], key=lambda p:(p["name"],p["version"])):
            if package["source"] is None:
                continue
            notices.append(f"\n{'='*72}\n{package['name']} {package['version']}\nLicense: {package['license']}\n{package.get('repository') or ''}\n")
            directory = Path(package["manifest_path"]).parent
            paths = sorted({p for pattern in ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*") for p in directory.glob(pattern) if p.is_file()})
            for path in paths:
                notices.append(path.name + "\n" + path.read_text(errors="replace") + "\n")
        files.append(("THIRD-PARTY-NOTICES.txt", "".join(notices).encode(), 0o644))
    else:
        # Only version-controlled inputs; never traverse local logs, target or caches.
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        for filename in filter(None, paths):
            path = ROOT / filename
            if path.is_file():
                files.append((filename, path.read_bytes(), 0o755 if os.access(path, os.X_OK) else 0o644))
    with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as tar:
        for filename, data, mode in sorted(files):
            info = tarfile.TarInfo("herdr-agent-grid/" + filename)
            info.size, info.mtime, info.mode = len(data), 0, mode
            tar.addfile(info, io.BytesIO(data))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(checksum + "  " + archive.name + "\n")
    print(archive)


if __name__ == "__main__":
    main()
