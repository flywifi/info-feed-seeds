#!/usr/bin/env python3
"""Print the decoded text of every allowed-corpus line a change adds, so a reviewer reads each text
that is allowed to pass (audit 6, N5). Usage: python3 tools/corpus_added.py BASE_REF

Standard library only; ASCII output (non-ASCII is shown as escapes)."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = "tests/fixtures/allowed_corpus.jsonl"


def entries(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if line.strip():
            entry = json.loads(line)
            out[entry["id"]] = base64.b64decode(entry["b64"]).decode("utf-8")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: corpus_added.py BASE_REF")
        return 2
    proc = subprocess.run(["git", "show", f"{argv[0]}:{PATH}"], cwd=ROOT, capture_output=True)
    before = entries(proc.stdout.decode("utf-8")) if proc.returncode == 0 else {}
    now = entries((ROOT / PATH).read_text(encoding="utf-8"))
    added = {i: t for i, t in now.items() if before.get(i) != t}
    print(f"corpus_added: {len(added)} allowed line(s) added or changed since {argv[0]}")
    for i, text in added.items():
        print(f"  {i}: {ascii(text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
