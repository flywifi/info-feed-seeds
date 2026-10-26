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

from urllib.parse import urldefrag, urlsplit

from ..items import RawItem
from . import feed, files, page
from .base import AdapterError

ADAPTERS = {
    "feed": feed.parse,
    "page": page.parse,
    "sitemap": files.parse_sitemap,
    "xml": files.parse_xml,
    "json-api": files.parse_json_api,
    "urllist": files.parse_urllist,
    "artifact": files.parse_artifact,
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
PAGE_SELECTOR_KEYS = ("title_selector", "link_selector", "date_selector", "summary_selector", "next_selector")

# The route kinds each adapter reads; `kind` names the document, `adapter` the reader.
ALLOWED_KINDS = {
    "feed": ("rss", "atom", "rdf", "jsonfeed"),
    "page": ("page",),
    "sitemap": ("sitemap",),
    "xml": ("xml",),
    "json-api": ("json-api",),
    "urllist": ("urllist",),
    "artifact": ("artifact",),
}

DEFAULT_FOLLOW_LIMIT = 3
DEFAULT_PORTS = {"http": 80, "https": 443}


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
    """Configuration defects in one route, as lint lines without the source prefix. The schema already
    refuses unknown params and missing xml or json-api keys; this reads what the schema cannot."""
    adapter = route.get("adapter")
    params = route.get("params") or {}
    problems = []
    if adapter not in REQUIRED_PARAMS:
        problems.append(f"route {route.get('url')}: unknown adapter {adapter!r}")
        return problems
    if route.get("kind") not in ALLOWED_KINDS[adapter]:
        problems.append(f"route {route.get('url')}: kind {route.get('kind')!r} does not fit adapter {adapter!r}")
    if adapter == "page" and any(k in params for k in PAGE_SELECTOR_KEYS) and "item_selector" not in params:
        problems.append(f"route {route.get('url')}: page selectors need params.item_selector")
    return problems


def follow_limit(route: dict) -> int:
    """How many `follow` URLs the poller may fetch for this route: max_sitemaps or max_pages when the
    manifest sets one (0 switches following off), else 3."""
    params = route.get("params") or {}
    for key in ("max_sitemaps", "max_pages"):
        value = params.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    return DEFAULT_FOLLOW_LIMIT


def check_result(parsed) -> dict:
    """The adapter's return value when it has the shared shape, else TypeError, so a broken adapter
    fails its own route instead of the poll run."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise TypeError("adapter result has no items list")
    if not all(isinstance(item, RawItem) for item in parsed["items"]):
        raise TypeError("adapter result items are not RawItem objects")
    if not isinstance(parsed.get("follow") or [], list):
        raise TypeError("adapter result follow is not a list")
    return parsed


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) with the scheme's default port read as None; None for a bad port."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    return scheme, (parts.hostname or "").lower(), None if port == DEFAULT_PORTS.get(scheme) else port


def follow_url(route_url: str, candidate) -> str | None:
    """The follow URL the poller may fetch, or None: http or https only, the same host and port as the
    route, never from https down to http, fragment dropped. Anything an adapter returns is data from the
    fetched document, never trusted."""
    if not isinstance(candidate, str):
        return None
    clean = urldefrag(candidate.strip())[0]
    cand, base = _origin(clean), _origin(route_url)
    if cand is None or base is None or cand[0] not in DEFAULT_PORTS or not cand[1]:
        return None
    if cand[1:] != base[1:] or (base[0] == "https" and cand[0] != "https"):
        return None
    return clean


def live_proof(attempt: dict | None) -> bool:
    """A proof record: the latest attempt on a route was a live 200 that parsed at least one item."""
    return bool(attempt and attempt.get("source_label") == "live" and attempt.get("http_status") == 200
                and attempt.get("result") == "ok" and attempt.get("tried_at") and attempt.get("sample"))


__all__ = ["ADAPTERS", "ALLOWED_KINDS", "AdapterError", "DEFAULT_FOLLOW_LIMIT", "DEFAULT_PORTS", "PAGE_SELECTOR_KEYS",
           "REQUIRED_PARAMS", "check_result", "configured", "feed", "files", "follow_limit", "follow_url", "get_adapter",
           "lint_route", "live_proof", "page"]
