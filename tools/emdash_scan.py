#!/usr/bin/env python3
"""Scan tracked text files for em dashes (U+2014). Exit 1 when any are found.

Usage: python3 tools/emdash_scan.py [ROOT]

Tracked files come from `git ls-files`; without git the whole tree is walked, skipping `.git`,
ignored state directories and binaries. Output is ASCII only: path:line:column per hit.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EM_DASH = "\u2014"
SKIP_DIRS = {".git", ".cache", "local", "logs", ".venv", ".venv-crawl", "__pycache__", "dist"}
ROOT = Path(__file__).resolve().parent.parent


def tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run([ "git", "-C", str(root), "ls-files", "-z"], check=True,
                             capture_output=True).stdout
        return [root / p.decode("utf-8") for p in out.split(b"\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            files.extend(Path(dirpath) / f for f in filenames)
        return files


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def scan(root: Path = ROOT, files: list[Path] | None = None) -> list[tuple[str, int, int]]:
    """Return (relative path, line, column) for every em dash in the given files."""
    hits: list[tuple[str, int, int]] = []
    for path in files if files is not None else tracked_files(root):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _is_binary(data):
            continue
        text = data.decode("utf-8", errors="replace")
        if EM_DASH not in text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            col = line.find(EM_DASH)
            while col != -1:
                hits.append((str(path.relative_to(root)), line_no, col + 1))
                col = line.find(EM_DASH, col + 1)
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve() if argv else ROOT
    hits = scan(root)
    for rel, line, col in hits:
        print(f"{rel}:{line}:{col}: em dash")
    print(f"em dash scan: {len(hits)} hit(s) in tracked text")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
