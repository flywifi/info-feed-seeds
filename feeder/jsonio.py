"""The single writer for this repository.

Every JSON file the feeder writes goes through `dump_json`: keys sorted, two-space indent, UTF-8,
LF line endings, trailing newline, written to a temporary file in the same directory and moved into
place with `os.replace` so a crash never leaves a half-written file behind. Text outputs (digest
Markdown, run summaries, commit messages) go through `write_text`, which uses the same atomic path
and forces LF. The `*_if_changed` forms compare bytes first and leave an identical file untouched.
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


def _umask() -> int:
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


# mkstemp creates 0600 files; written outputs get the usual mode for the process umask.
_FILE_MODE = 0o666 & ~_umask()


def _write_bytes(target: Path, data: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, _FILE_MODE)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def dump_json(path: str | os.PathLike, obj: Any) -> Path:
    """Write `obj` to `path` atomically in canonical form. Returns the path written."""
    return _write_bytes(Path(path), dumps(obj).encode("utf-8"))


def text_bytes(text: str) -> bytes:
    """UTF-8 bytes of `text` with every line ending forced to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def write_text(path: str | os.PathLike, text: str) -> Path:
    """Write text atomically as UTF-8 with LF line endings."""
    return _write_bytes(Path(path), text_bytes(text))


def write_bytes_if_changed(path: str | os.PathLike, data: bytes) -> bool:
    """Write `data` only when the file is missing or its bytes differ. True when written."""
    target = Path(path)
    try:
        if target.read_bytes() == data:
            return False
    except OSError:
        pass
    _write_bytes(target, data)
    return True


def write_text_if_changed(path: str | os.PathLike, text: str) -> bool:
    return write_bytes_if_changed(path, text_bytes(text))


def dump_json_if_changed(path: str | os.PathLike, obj: Any) -> bool:
    return write_bytes_if_changed(path, dumps(obj).encode("utf-8"))


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


__all__ = [
    "dump_json", "dump_json_if_changed", "dumps", "is_canonical", "load_json", "text_bytes",
    "write_bytes_if_changed", "write_text", "write_text_if_changed",
]
