"""Route adapters. Each adapter turns a fetched body into feed metadata plus RawItem objects.

`ADAPTERS` maps the adapter names a source manifest may use (the `adapter` enum in
schema/source.schema.json) to a parse function. Every parse function takes the body bytes and the
keyword arguments `url` and `params`, returns {"kind", "items": [RawItem], "follow": [url, ...]}
and raises AdapterError for a body it cannot read. `follow` lists further URLs the poller may fetch
with the same adapter (child sitemaps, next pages), bounded by `follow_limit`.

A route is configured when its adapter is registered here and its params carry the keys in
`REQUIRED_PARAMS`. Anything else is a landing route: kept in the manifest, recorded by hand in
methods.json for reachability, never fetched by the poller. A page route with empty params is the
usual landing route."""
from __future__ import annotations

from . import feed
from .base import AdapterError

ADAPTERS = {
    "feed": feed.parse,
}

# Params a route must carry before the poller reads it, by adapter name (every name the schema allows).
REQUIRED_PARAMS = {
    "feed": (),
    "page": ("item_selector",),
    "sitemap": (),
    "xml": ("item_path", "fields"),
    "json-api": ("items_path", "fields"),
    "urllist": (),
    "artifact": (),
}

# A page route that names any of these without item_selector is a configuration error, not a landing route.
PAGE_SELECTOR_KEYS = ("title_selector", "link_selector", "date_selector", "summary_selector")

DEFAULT_FOLLOW_LIMIT = 3


def get_adapter(name: str):
    try:
        return ADAPTERS[name]
    except KeyError:
        raise KeyError(f"no adapter named {name!r}; known: {sorted(ADAPTERS)}") from None


def configured(route: dict) -> bool:
    """True when the poller can read this route: a registered adapter and every required param."""
    adapter = route.get("adapter")
    if adapter not in ADAPTERS:
        return False
    params = route.get("params") or {}
    return all(key in params for key in REQUIRED_PARAMS.get(adapter, ()))


def lint_route(route: dict) -> list[str]:
    """Configuration defects in one route, as lint lines without the source prefix."""
    adapter = route.get("adapter")
    params = route.get("params") or {}
    problems = []
    if adapter not in REQUIRED_PARAMS:
        problems.append(f"route {route.get('url')}: unknown adapter {adapter!r}")
        return problems
    if adapter == "page" and any(k in params for k in PAGE_SELECTOR_KEYS) and "item_selector" not in params:
        problems.append(f"route {route.get('url')}: page selectors need params.item_selector")
    if adapter in ("xml", "json-api") and params:
        for key in REQUIRED_PARAMS[adapter]:
            if key not in params:
                problems.append(f"route {route.get('url')}: {adapter} route needs params.{key}")
    return problems


def follow_limit(route: dict) -> int:
    """How many `follow` URLs the poller may fetch for this route (max_sitemaps or max_pages, else 3)."""
    params = route.get("params") or {}
    for key in ("max_sitemaps", "max_pages"):
        value = params.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return DEFAULT_FOLLOW_LIMIT


__all__ = ["ADAPTERS", "AdapterError", "DEFAULT_FOLLOW_LIMIT", "PAGE_SELECTOR_KEYS", "REQUIRED_PARAMS",
           "configured", "feed", "follow_limit", "get_adapter", "lint_route"]
