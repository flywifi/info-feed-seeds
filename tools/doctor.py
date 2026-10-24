#!/usr/bin/env python3
"""Health checker for info-feed-seeds.

Usage: python3 tools/doctor.py check

Exit codes: 0 clean (advisories allowed and counted), 1 a repository defect, 2 a usage error, 3 the
environment was blocked or a required check did not run, 4 the command is not implemented yet. 3 is
never reported as clean. An advisory check that cannot run is reported as "advisory: blocked" and
does not change the exit code.

Only `check` exists so far. `fix --safe` and `propose`, described in AGENTS.md, are planned.
Output is ASCII only. Standard library only.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from doctor_checks import CHECKS  # noqa: E402


def say(message: str) -> None:
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def check(root: Path = ROOT) -> int:
    defects = blocked = advisories = 0
    for module in CHECKS:
        try:
            status, message = module.run(root)
        except Exception as exc:  # noqa: BLE001  a crashing check is a check that did not run
            status, message = "blocked", f"check crashed: {type(exc).__name__}: {exc}"
        kind = "required" if module.REQUIRED else "advisory"
        say(f"doctor: {module.NAME} ({kind}): {status}: {message}")
        if status == "defect":
            defects += 1
        elif status == "blocked" and module.REQUIRED:
            blocked += 1
        elif status in ("advisory", "blocked"):
            advisories += 1
    if blocked:
        say(f"doctor: {blocked} required check(s) did not run; not clean")
        return 3
    if defects:
        say(f"doctor: {defects} defect(s)")
        return 1
    say(f"doctor: clean, {advisories} advisory note(s)" if advisories else "doctor: clean")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        return check()
    if argv and argv[0] in ("fix", "propose"):
        say(f"doctor: {argv[0]} is not implemented yet; only check exists")
        return 4
    say("usage: python3 tools/doctor.py check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
