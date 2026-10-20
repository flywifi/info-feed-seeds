"""Feed adapter: RSS 2.0, RSS 1.0 (RDF), Atom and JSON Feed.

Namespaces read: Dublin Core, content:encoded, iTunes podcast, Media RSS and the YouTube feed
namespace. Every lookup is an explicit `is not None` check: `find(a) or find(b)` is wrong on
ElementTree because an element with no children is falsy, and that bug is not allowed to recur.
Documents containing `<!ENTITY` are refused before parsing (entity expansion attacks).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from ..items import RawItem, to_utc_z

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "podcast": "https://podcastindex.org/namespace/1.0",
}
_ENTITY_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)
_JSONFEED_VERSION = "https://jsonfeed.org/version/"


class FeedParseError(ValueError):
    """The document is not a feed this adapter can read."""


class FeedRefused(FeedParseError):
    """The document was refused before parsing (for example it declares entities)."""


# --------------------------------------------------------------------------- helpers

def _ns(prefix: str, local: str) -> str:
    return "{%s}%s" % (NS[prefix], local)


def _text(el: ET.Element | None, *paths: str) -> str | None:
    """First non-empty text among the child paths, or None. Explicit None checks throughout."""
    if el is None:
        return None
    for path in paths:
        child = el.find(path)
        if child is not None and child.text is not None:
            value = child.text.strip()
            if value:
                return value
    return None


def _all_text(el: ET.Element | None, path: str) -> list[str]:
    if el is None:
        return []
    out = []
    for child in el.findall(path):
        if child.text is not None and child.text.strip():
            out.append(child.text.strip())
    return out


def _attr(el: ET.Element | None, path: str, name: str) -> str | None:
    if el is None:
        return None
    child = el.find(path)
    if child is None:
        return None
    value = child.get(name)
    return value.strip() if value else None


def _detect_bytes(data: bytes | str) -> bytes:
    if isinstance(data, str):
        return data.encode("utf-8")
    return data


def detect_kind(data: bytes | str) -> str | None:
    """'jsonfeed', 'atom', 'rdf', 'rss' or None, from the document head alone."""
    raw = _detect_bytes(data)
    head = raw.lstrip(b"\xef\xbb\xbf \t\r\n")[:4096]
    if head.startswith(b"{"):
        return "jsonfeed"
    lowered = head.lower()
    if b"<feed" in lowered and b"2005/atom" in lowered:
        return "atom"
    if b"<rdf:rdf" in lowered:
        return "rdf"
    if b"<rss" in lowered:
        return "rss"
    return None


# --------------------------------------------------------------------------- RSS 2.0

def _media_extra(el: ET.Element) -> dict:
    extra: dict = {}
    enclosures = []
    for enc in el.findall("enclosure"):
        if enc.get("url"):
            enclosures.append({"url": enc.get("url"), "type": enc.get("type"), "length": enc.get("length")})
    if enclosures:
        extra["enclosures"] = enclosures
    duration = _text(el, _ns("itunes", "duration"))
    if duration is not None:
        extra["itunes_duration"] = duration
    episode = _text(el, _ns("itunes", "episode"))
    if episode is not None:
        extra["itunes_episode"] = episode
    image = _attr(el, _ns("itunes", "image"), "href")
    if image is not None:
        extra["itunes_image"] = image
    contents = []
    for mc in el.findall(_ns("media", "content")) + el.findall(_ns("media", "group") + "/" + _ns("media", "content")):
        if mc.get("url"):
            contents.append({"url": mc.get("url"), "type": mc.get("type"), "medium": mc.get("medium")})
    if contents:
        extra["media_content"] = contents
    thumb = _attr(el, _ns("media", "thumbnail"), "url")
    if thumb is None:
        thumb = _attr(el, _ns("media", "group") + "/" + _ns("media", "thumbnail"), "url")
    if thumb is not None:
        extra["media_thumbnail"] = thumb
    group = el.find(_ns("media", "group"))
    if group is not None:
        title = _text(group, _ns("media", "title"))
        if title is not None:
            extra["media_title"] = title
        description = _text(group, _ns("media", "description"))
        if description is not None:
            extra["media_description"] = description
    video_id = _text(el, _ns("yt", "videoId"))
    if video_id is not None:
        extra["yt_video_id"] = video_id
    channel_id = _text(el, _ns("yt", "channelId"))
    if channel_id is not None:
        extra["yt_channel_id"] = channel_id
    categories = _all_text(el, "category") + _all_text(el, _ns("dc", "subject"))
    if categories:
        extra["categories"] = categories
    return extra


def _rss_item(el: ET.Element) -> RawItem:
    guid_el = el.find("guid")
    guid = guid_el.text.strip() if guid_el is not None and guid_el.text and guid_el.text.strip() else None
    link = _text(el, "link")
    if link is None and guid_el is not None and guid and (guid_el.get("isPermaLink") or "true").lower() == "true" \
            and guid.startswith(("http://", "https://")):
        link = guid
    if link is None:
        atom_link = el.find(_ns("atom", "link"))
        if atom_link is not None and atom_link.get("href"):
            link = atom_link.get("href").strip()
    summary = _text(el, "description")
    extra = _media_extra(el)
    encoded = _text(el, _ns("content", "encoded"))
    if encoded is not None:
        extra["content_encoded"] = encoded
        if summary is None:
            summary = encoded
    authors = _all_text(el, _ns("dc", "creator")) + _all_text(el, "author") + _all_text(el, _ns("itunes", "author"))
    published = to_utc_z(_text(el, "pubDate", _ns("dc", "date"), "lastBuildDate"))
    return RawItem(guid=guid, link=link, title=_text(el, "title") or "", summary=summary or "",
                   published=published, authors=authors, extra=extra)


def _parse_rss(root: ET.Element) -> dict:
    channel = root.find("channel")
    if channel is None:
        raise FeedParseError("rss document has no channel")
    items = [_rss_item(el) for el in channel.findall("item")]
    return {"kind": "rss", "title": _text(channel, "title"), "link": _text(channel, "link"),
            "description": _text(channel, "description"), "items": items}


# --------------------------------------------------------------------------- RSS 1.0 (RDF)

def _rdf_item(el: ET.Element) -> RawItem:
    about = el.get(_ns("rdf", "about"))
    link = _text(el, _ns("rss1", "link"))
    if link is None and about and about.startswith(("http://", "https://")):
        link = about
    summary = _text(el, _ns("rss1", "description"))
    extra = _media_extra(el)
    encoded = _text(el, _ns("content", "encoded"))
    if encoded is not None:
        extra["content_encoded"] = encoded
        if summary is None:
            summary = encoded
    authors = _all_text(el, _ns("dc", "creator"))
    published = to_utc_z(_text(el, _ns("dc", "date")))
    return RawItem(guid=about.strip() if about else None, link=link, title=_text(el, _ns("rss1", "title")) or "",
                   summary=summary or "", published=published, authors=authors, extra=extra)


def _parse_rdf(root: ET.Element) -> dict:
    channel = root.find(_ns("rss1", "channel"))
    items = [_rdf_item(el) for el in root.findall(_ns("rss1", "item"))]
    return {"kind": "rdf", "title": _text(channel, _ns("rss1", "title")),
            "link": _text(channel, _ns("rss1", "link")),
            "description": _text(channel, _ns("rss1", "description")), "items": items}


# --------------------------------------------------------------------------- Atom

def _atom_link(el: ET.Element) -> str | None:
    """Prefer rel=alternate (or no rel), then any href."""
    links = el.findall(_ns("atom", "link"))
    for link in links:
        rel = (link.get("rel") or "alternate").lower()
        if rel == "alternate" and link.get("href"):
            return link.get("href").strip()
    for link in links:
        if link.get("href"):
            return link.get("href").strip()
    return None


def _atom_entry(el: ET.Element) -> RawItem:
    summary = _text(el, _ns("atom", "summary"))
    content = _text(el, _ns("atom", "content"))
    extra = _media_extra(el)
    if content is not None:
        extra["content"] = content
        if summary is None:
            summary = content
    if summary is None and "media_description" in extra:
        summary = extra["media_description"]
    authors = []
    for author in el.findall(_ns("atom", "author")):
        name = _text(author, _ns("atom", "name"))
        if name is not None:
            authors.append(name)
    published = to_utc_z(_text(el, _ns("atom", "published"), _ns("atom", "updated")))
    return RawItem(guid=_text(el, _ns("atom", "id")), link=_atom_link(el),
                   title=_text(el, _ns("atom", "title")) or "", summary=summary or "",
                   published=published, authors=authors, extra=extra)


def _parse_atom(root: ET.Element) -> dict:
    items = [_atom_entry(el) for el in root.findall(_ns("atom", "entry"))]
    return {"kind": "atom", "title": _text(root, _ns("atom", "title")), "link": _atom_link(root),
            "description": _text(root, _ns("atom", "subtitle")), "items": items}


# --------------------------------------------------------------------------- JSON Feed

def _jsonfeed_item(entry: dict) -> RawItem:
    if not isinstance(entry, dict):
        raise FeedParseError("json feed item is not an object")
    authors = []
    for author in entry.get("authors") or ([entry["author"]] if isinstance(entry.get("author"), dict) else []):
        if isinstance(author, dict) and author.get("name"):
            authors.append(str(author["name"]).strip())
    summary = entry.get("summary") or entry.get("content_text") or entry.get("content_html") or ""
    extra: dict = {}
    for key in ("content_html", "content_text", "image", "banner_image", "tags", "attachments", "external_url", "language"):
        if entry.get(key) is not None:
            extra[key] = entry[key]
    guid = entry.get("id")
    link = entry.get("url") or entry.get("external_url")
    return RawItem(guid=str(guid).strip() if guid is not None else None,
                   link=str(link).strip() if link else None,
                   title=str(entry.get("title") or "").strip(), summary=str(summary),
                   published=to_utc_z(entry.get("date_published") or entry.get("date_modified")),
                   authors=authors, extra=extra)


def _parse_jsonfeed(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FeedParseError(f"json feed is not valid json: {exc}") from None
    if not isinstance(data, dict) or not str(data.get("version", "")).startswith(_JSONFEED_VERSION):
        raise FeedParseError("json document is not a JSON Feed (missing jsonfeed.org version)")
    items = data.get("items")
    if not isinstance(items, list):
        raise FeedParseError("json feed has no items array")
    return {"kind": "jsonfeed", "title": data.get("title"), "link": data.get("home_page_url"),
            "description": data.get("description"), "items": [_jsonfeed_item(e) for e in items]}


# --------------------------------------------------------------------------- entry point

def parse_feed(data: bytes | str) -> dict:
    """Parse a feed document. Returns {"kind", "title", "link", "description", "items": [RawItem]}.
    Raises FeedRefused for documents declaring entities and FeedParseError for anything that is
    not a readable feed."""
    raw = _detect_bytes(data)
    if _ENTITY_RE.search(raw):
        raise FeedRefused("document declares an entity (<!ENTITY); refused")
    kind = detect_kind(raw)
    if kind == "jsonfeed":
        return _parse_jsonfeed(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedParseError(f"xml parse error: {exc}") from None
    tag = root.tag
    if tag == _ns("atom", "feed"):
        return _parse_atom(root)
    if tag == _ns("rdf", "RDF"):
        return _parse_rdf(root)
    if tag == "rss":
        return _parse_rss(root)
    raise FeedParseError(f"root element {tag!r} is not a feed")


__all__ = ["FeedParseError", "FeedRefused", "NS", "detect_kind", "parse_feed"]
