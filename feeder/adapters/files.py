"""Adapters for structured files that are not feeds: XML sitemaps, URL lists, XML documents, JSON APIs
and single files watched for change. Each `parse_*` takes the body bytes and the keyword arguments `url`
and `params` and returns the shared {"kind", "items", "follow"} shape (see feeder/adapters/__init__.py).

- sitemap: a urlset yields one item per <loc> with <lastmod> as its date; a sitemapindex yields its child
  sitemaps as follow URLs. Params: lastmod_since (YYYY-MM-DD), path_prefix, max_sitemaps. Gzip bodies are
  read as is.
- urllist: one item per http(s) URL in a text file; '#' starts a comment; duplicates dropped.
- xml: item_path and field paths in ElementTree's path subset ('name@attr' reads an attribute, '.' the
  item text), namespaces as a prefix map. Fields: title, link, guid, summary, published.
- json-api: items_path and field paths ('results[].title'; '[]' maps over a list), next_path for the
  next page URL, max_pages.
- artifact: one item per distinct file content, keyed by its sha256, so the store adds a new item only
  when the file changes. Param: label.

Documents that declare XML entities are refused. Dates go through items.to_utc_z; one that does not
parse stays null."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET

from ..items import RawItem, to_utc_z
from .base import AdapterError

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
ITEM_FIELDS = ("title", "link", "guid", "summary", "published")


def _xml_root(data: bytes) -> ET.Element:
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise AdapterError(f"bad gzip body: {exc}") from None
    if re.search(rb"<!ENTITY", data[:65536], re.I):
        raise AdapterError("document declares entities")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise AdapterError(f"not well-formed XML: {exc}") from None


def _item(values: dict) -> RawItem | None:
    title, link = values.get("title"), values.get("link")
    if not title and not link:
        return None
    return RawItem(guid=values.get("guid") or link or title, link=link, title=title or link,
                   summary=values.get("summary") or "", published=to_utc_z(values.get("published")))


def parse_sitemap(data: bytes, *, url: str | None = None, params: dict | None = None) -> dict:
    params = params or {}
    root = _xml_root(data)
    if root.tag == f"{SM}sitemapindex":
        follow = [(s.findtext(f"{SM}loc") or "").strip() for s in root.findall(f"{SM}sitemap")]
        return {"kind": "sitemapindex", "items": [], "follow": [u for u in follow if u]}
    if root.tag != f"{SM}urlset":
        raise AdapterError("not a sitemap")
    since, prefix, items = params.get("lastmod_since"), params.get("path_prefix") or "", []
    for node in root.findall(f"{SM}url"):
        loc = (node.findtext(f"{SM}loc") or "").strip()
        lastmod = to_utc_z(node.findtext(f"{SM}lastmod"))
        if not loc or (prefix and prefix not in loc) or (since and lastmod and lastmod[:10] < since):
            continue
        items.append(RawItem(guid=loc, link=loc, title=loc, published=lastmod))
    return {"kind": "sitemap", "items": items, "follow": []}


def parse_urllist(data: bytes, *, url: str | None = None, params: dict | None = None) -> dict:
    seen, items = set(), []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.split("#", 1)[0].strip().lstrip("\ufeff")
        if not re.match(r"https?://\S+$", line, re.I) or line in seen:
            continue
        seen.add(line)
        items.append(RawItem(guid=line, link=line, title=line))
    return {"kind": "urllist", "items": items, "follow": []}


def _xml_text(node: ET.Element, path: str, ns: dict) -> str | None:
    if path == ".":
        return (node.text or "").strip() or None
    el_path, _, attr = path.partition("@")
    target = node if el_path in ("", ".") else node.find(el_path, ns)
    if target is None:
        return None
    value = target.get(attr) if attr else "".join(target.itertext())
    return (value or "").strip() or None


def parse_xml(data: bytes, *, url: str | None = None, params: dict | None = None) -> dict:
    params = params or {}
    fields, ns = params.get("fields") or {}, params.get("namespaces") or {}
    if not params.get("item_path") or not fields:
        raise AdapterError("xml route needs item_path and fields")
    root = _xml_root(data)
    try:
        nodes = root.findall(params["item_path"], ns)
        rows = [{k: _xml_text(n, fields[k], ns) for k in ITEM_FIELDS if k in fields} for n in nodes]
    except (SyntaxError, KeyError) as exc:
        raise AdapterError(f"bad xml path: {exc}") from None
    return {"kind": "xml", "items": [i for i in map(_item, rows) if i], "follow": []}


def json_path(obj, path: str):
    """'results[].title' walks dict keys; a '[]' step maps over a list. A value, a list, or None."""
    cur = obj
    for step in [s for s in path.replace("[]", ".[]").split(".") if s]:
        if step == "[]":
            if not isinstance(cur, list):
                return None
            continue
        if isinstance(cur, list):
            cur = [c.get(step) if isinstance(c, dict) else None for c in cur]
        elif isinstance(cur, dict):
            cur = cur.get(step)
        else:
            return None
    return cur


def parse_json_api(data: bytes, *, url: str | None = None, params: dict | None = None) -> dict:
    params = params or {}
    fields = params.get("fields") or {}
    if not params.get("items_path") or not fields:
        raise AdapterError("json-api route needs items_path and fields")
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AdapterError(f"not JSON: {exc}") from None
    rows = json_path(doc, params["items_path"])
    if not isinstance(rows, list):
        raise AdapterError(f"items_path {params['items_path']!r} did not reach a list")
    items = []
    for row in rows:
        if isinstance(row, dict):
            values = {k: json_path(row, fields[k]) for k in ITEM_FIELDS if k in fields}
            values = {k: str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                      for k, v in values.items() if v is None or isinstance(v, (str, int, float))}
            item = _item(values)
            if item:
                items.append(item)
    nxt = json_path(doc, params["next_path"]) if params.get("next_path") else None
    return {"kind": "json-api", "items": items, "follow": [nxt] if isinstance(nxt, str) and nxt else []}


def parse_artifact(data: bytes, *, url: str | None = None, params: dict | None = None) -> dict:
    params = params or {}
    sha = hashlib.sha256(data).hexdigest()
    label = params.get("label") or (url or "").rstrip("/").rsplit("/", 1)[-1] or "file"
    item = RawItem(guid=f"{url}#sha256:{sha}", link=url, title=f"File changed: {label}",
                   summary=f"sha256 {sha}, {len(data)} bytes", extra={"sha256": sha, "bytes": len(data)})
    return {"kind": "artifact", "items": [item], "follow": []}


__all__ = ["json_path", "parse_artifact", "parse_json_api", "parse_sitemap", "parse_urllist", "parse_xml"]
