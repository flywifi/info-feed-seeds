"""Doctor checks. Each module exposes NAME, REQUIRED (bool) and run(root) -> (status, message), where
status is "ok", "advisory", "defect" or "blocked". A required check that is blocked makes the doctor
exit 3; an advisory never changes the exit code."""
from __future__ import annotations

from . import config_lint, hooks

CHECKS = (config_lint, hooks)

__all__ = ["CHECKS"]
