"""Route adapters. Each adapter turns a fetched body into feed metadata plus RawItem objects."""
from __future__ import annotations

from . import feed

ADAPTERS = {
    "feed": feed.parse_feed,
}


def get_adapter(name: str):
    try:
        return ADAPTERS[name]
    except KeyError:
        raise KeyError(f"no adapter named {name!r}; known: {sorted(ADAPTERS)}") from None


__all__ = ["ADAPTERS", "feed", "get_adapter"]
