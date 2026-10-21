"""Required: the repository config passes `hub lint` (sources, profiles, releases, canonical JSON)."""
from __future__ import annotations

import sys
from pathlib import Path

NAME = "config-lint"
REQUIRED = True


def run(root: Path) -> tuple[str, str]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from feeder.config import Repo, lint
    except ImportError as exc:
        return "blocked", f"cannot import the feeder package: {exc}"
    problems = lint(Repo(root=root))
    if problems:
        return "defect", f"{len(problems)} lint problem(s); first: {problems[0]}"
    return "ok", "hub lint is clean"
