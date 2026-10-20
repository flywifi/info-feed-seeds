"""The single JSON writer for this repository.

Every JSON file the feeder writes goes through `dump_json`: keys sorted, two-space indent, UTF-8,
LF line endings, trailing newline, written to a temporary file in the same directory and moved into
place with `os.replace` so a crash never leaves a half-written file behind.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def dumps(obj: Any) -> str:
    """Canonical text form: sorted keys, indent 2, non-ASCII kept as is, one trailing LF."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def dump_json(path: str | os.PathLike, obj: Any) -> Path:
    """Write `obj` to `path` atomically in canonical form. Returns the path written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = dumps(obj).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def load_json(path: str | os.PathLike) -> Any:
    """Read a JSON file as UTF-8."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_canonical(path: str | os.PathLike) -> bool:
    """True when the file's bytes equal the canonical form of its own content."""
    raw = Path(path).read_bytes()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return raw == dumps(obj).encode("utf-8")


__all__ = ["dump_json", "dumps", "is_canonical", "load_json"]
