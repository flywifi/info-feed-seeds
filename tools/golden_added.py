#!/usr/bin/env python3
"""Print every golden case a change adds or changes, decoded, with its expected verdicts, so a
reviewer reads each text the checker is now expected to block, advise on or pass.
Usage: python3 tools/golden_added.py BASE_REF [ROOT]

CI runs the base branch's copy of this file, so a pull request cannot change what it prints.
Standard library only; ASCII output (non-ASCII is shown as escapes)."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = "tests/fixtures/golden_cases.jsonl"


def entries(text: str) -> dict[str, dict]:
    out = {}
    for line in text.splitlines():
        if line.strip():
            entry = json.loads(line)
            out[entry["id"]] = entry
    return out


def report(before: dict, now: dict, base: str) -> list[str]:
    added = {i: e for i, e in now.items() if before.get(i) != e}
    removed = sorted(set(before) - set(now))
    lines = [f"golden_added: {PATH}: {len(added)} case(s) added or changed, {len(removed)} removed since {base}"]
    for i, e in added.items():
        text = base64.b64decode(e["b64"]).decode("utf-8")
        lines.append(f"  {i} [{e['kind']}, {e['rule']}] expect {json.dumps(e['expect'], sort_keys=True)}: {ascii(text)}")
    lines += [f"  removed: {i}" for i in removed]
    return lines


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print("usage: golden_added.py BASE_REF [ROOT]")
        return 2
    root = Path(argv[1]).resolve() if len(argv) == 2 else ROOT
    proc = subprocess.run(["git", "show", f"{argv[0]}:{PATH}"], cwd=root, capture_output=True)
    before = entries(proc.stdout.decode("utf-8")) if proc.returncode == 0 else {}
    now = entries((root / PATH).read_text(encoding="utf-8"))
    for line in report(before, now, argv[0]):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
