#!/usr/bin/env python3
"""Compatibility entrypoint; the application runs entirely in the native binary."""
import os
from pathlib import Path
import sys
if __name__ == "__main__":
    os.execv("/bin/sh", ["sh", str(Path(__file__).resolve().with_name("run.sh")), *sys.argv[1:]])
