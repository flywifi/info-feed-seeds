"""info-feed-seeds feeder package.

Standard library only. `__version__` is read from the repository's `VERSION` file so the
User-Agent string and the package never disagree.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = ROOT / "VERSION"


def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


__version__ = _read_version()
USER_AGENT = f"info-feed-seeds/{__version__} (+https://github.com/flywifi/info-feed-seeds)"

__all__ = ["ROOT", "USER_AGENT", "__version__"]
