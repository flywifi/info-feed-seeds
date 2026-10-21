"""Advisory: a clone that can push should run the tracked hooks (git config core.hooksPath tools/hooks).
A pull-only clone (push URL disabled) and CI checkouts need no hooks; the hub never requires them."""
from __future__ import annotations

import subprocess
from pathlib import Path

NAME = "git-hooks"
REQUIRED = False
INSTALL = "git config core.hooksPath tools/hooks"


def _git(root: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else None


def run(root: Path) -> tuple[str, str]:
    push_url = _git(root, "remote", "get-url", "--push", "origin")
    if push_url is None:
        return "ok", "no origin remote; nothing pushes from here"
    if "flywifi/info-feed-seeds" not in push_url:
        return "ok", "push URL is disabled or elsewhere; this clone does not push here"
    hooks_path = _git(root, "config", "--get", "core.hooksPath")
    if hooks_path in ("tools/hooks", str(root / "tools" / "hooks")):
        return "ok", "tracked hooks are installed"
    return "advisory", f"this clone can push but the tracked hooks are not installed; run: {INSTALL}"
