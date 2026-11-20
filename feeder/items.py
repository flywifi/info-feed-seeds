"""Feed items: raw fields, identity, display sanitising, link normalisation and keyword tags.

Raw fields keep the feed's own text. Display fields are what a digest prints: HTML stripped, control,
zero-width and bidi characters removed, whitespace collapsed, em and en dashes replaced with " - ",
the summary capped at 300 characters, and e-mail addresses redacted (display only; the raw text is
untouched). `published` is a UTC timestamp with a Z suffix or null; it is never invented.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

DISPLAY_SUMMARY_CAP = 300
EMAIL_REDACTION = "[email redacted]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WS_RE = re.compile(r"\s+")
_DASHES = {"\u2014": " - ", "\u2013": " - "}
_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u180e\u00ad")
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u061c")
_TRACKING_PARAM_RE = re.compile(r"^(?:utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|ref_src|yclid|_hsenc|_hsmi|oly_\w+|vero_\w+)$", re.I)


@dataclass
class RawItem:
    guid: str | None = None
    link: str | None = None
    title: str = ""
    summary: str = ""
    published: str | None = None
    authors: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


_SURROGATE = re.compile("[\ud800-\udfff]")


def encodable(value, escape: bool = False):
    """The value with every character that cannot be written as UTF-8 (a lone surrogate, which JSON
    allows) replaced, through lists and dicts: by '?' in text, or with `escape` by its percent-escaped
    bytes ('%ED%A0%80'), which keeps two different ids or links different. Dict keys are always
    escaped. Anything that is not text is returned as it is."""
    if isinstance(value, str):
        if escape:
            return _SURROGATE.sub(lambda m: quote(m.group().encode("utf-8", "surrogatepass")), value)
        return _SURROGATE.sub("?", value)
    if isinstance(value, list):
        return [encodable(v, escape) for v in value]
    if isinstance(value, dict):
        return {encodable(k, True): encodable(v, escape) for k, v in value.items()}
    return value


def encodable_item(raw: RawItem) -> RawItem:
    """A copy of a raw item whose text can all be encoded, so one bad character costs that character
    and not the poll: the id hash, the store and the output files all write UTF-8. The id and the link
    are escaped, the rest is replaced; labels nested too deep to walk are dropped and the item kept."""
    try:
        extra = encodable(raw.extra)
    except RecursionError:
        extra = {}
    return replace(raw, guid=encodable(raw.guid, True), link=encodable(raw.link, True), title=encodable(raw.title),
                   summary=encodable(raw.summary), published=encodable(raw.published),
                   authors=encodable(raw.authors), extra=extra)


# --------------------------------------------------------------------------- identity

def content_sha256(feed_id: str, guid: str | None, link: str | None, title: str) -> str:
    """sha256 of feed_id|guid or link|title.strip(). The stable identity used for dedup."""
    key = guid if guid else (link or "")
    material = f"{feed_id}|{key}|{(title or '').strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- dates

def to_utc_z(value: str | None) -> str | None:
    """Parse an RFC 822 or ISO 8601 timestamp into 'YYYY-MM-DDTHH:MM:SSZ'. None when absent or
    unparseable; a date with no timezone is taken as UTC, never guessed from the publisher."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- sanitising

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(text: str) -> str:
    """Drop tags, keep text, decode entities. Falls back to a regex strip on parser errors."""
    if "<" not in text and "&" not in text:
        return text
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001  the parser is defensive; a regex is the fallback
        return unescape(re.sub(r"<[^>]+>", " ", text))
    return "".join(parser.parts)


def _drop_invisible(text: str) -> str:
    out = []
    for ch in text:
        if ch in _ZERO_WIDTH or ch in _BIDI:
            continue
        if ch in "\n\r\t":
            out.append(" ")
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            continue
        out.append(ch)
    return "".join(out)


def sanitize(text: str | None, cap: int | None = None, redact_emails: bool = False) -> str:
    """Display form of a text field. HTML stripped, invisible characters removed, dashes replaced,
    whitespace collapsed, optionally e-mails redacted and length capped."""
    if not text:
        return ""
    value = strip_html(text)
    value = _drop_invisible(value)
    for dash, replacement in _DASHES.items():
        value = value.replace(dash, replacement)
    value = _WS_RE.sub(" ", value).strip()
    if redact_emails:
        value = _EMAIL_RE.sub(EMAIL_REDACTION, value)
    if cap is not None and len(value) > cap:
        value = value[: max(cap - 3, 0)].rstrip() + "..."
    return value


def display_fields(raw: RawItem) -> dict:
    return {
        "title": sanitize(raw.title, redact_emails=True),
        "summary": sanitize(raw.summary, cap=DISPLAY_SUMMARY_CAP, redact_emails=True),
        "link": normalize_link(raw.link) if raw.link else None,
    }


# --------------------------------------------------------------------------- links

def normalize_link(url: str) -> str:
    """Re-serialise a link: scheme and host lowercased, default port dropped, fragment dropped,
    tracking parameters removed, remaining query order kept, whitespace trimmed."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:   # not a URL that can be taken apart (a bad bracket, port or host): kept as written
        return text
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not host:
        return text
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    if parts.username:
        cred = parts.username + (f":{parts.password}" if parts.password else "")
        host = f"{cred}@{host}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _TRACKING_PARAM_RE.match(k)], doseq=True)
    path = parts.path or "/"
    return urlunsplit((scheme, host, path, query, ""))


def link_key(url: str | None) -> str | None:
    """A looser identity for 'the same page': no scheme, no leading www., no trailing slash,
    query sorted, tracking parameters dropped."""
    if not url:
        return None
    normalized = normalize_link(url)
    try:
        parts = urlsplit(normalized)
    except ValueError:
        return normalized or None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return normalized or None
    path = parts.path.rstrip("/") or ""
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)), doseq=True)
    return host + path + (f"?{query}" if query else "")


# --------------------------------------------------------------------------- keywords

def _keyword_re(keyword: str) -> re.Pattern:
    words = [re.escape(w) for w in keyword.lower().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)")


def match_keywords(text: str, include: list[str], exclude: list[str]) -> dict:
    """Lowercase, word-boundary matching. Returns {"matched": [...], "excluded": [...],
    "accepted": bool}. Exclude wins; an empty include list accepts everything not excluded."""
    haystack = (text or "").lower()
    excluded = [k for k in exclude if _keyword_re(k).search(haystack)]
    matched = [k for k in include if _keyword_re(k).search(haystack)]
    accepted = not excluded and (bool(matched) or not include)
    return {"matched": matched, "excluded": excluded, "accepted": accepted}


def tag_topics(text: str, topic_keywords: dict[str, list[str]]) -> list[str]:
    """Return the topic and topic/subtopic tags whose keywords match. A subtopic match also tags
    its parent topic. Sorted, unique."""
    haystack = (text or "").lower()
    tags: set[str] = set()
    for tag, keywords in topic_keywords.items():
        if any(_keyword_re(k).search(haystack) for k in keywords):
            tags.add(tag)
            topic = tag.split("/", 1)[0]
            tags.add(topic)
    return sorted(tags)


# --------------------------------------------------------------------------- assembly

def build_item(feed_id: str, route_url: str, raw: RawItem, retrieved_at: str | None = None) -> dict:
    """The item record described by schema/item.schema.json (without guard or topics)."""
    return {
        "feed_id": feed_id,
        "route_url": route_url,
        "guid": raw.guid,
        "link": raw.link,
        "title": raw.title or "",
        "summary": raw.summary or "",
        "published": raw.published,
        "authors": list(raw.authors),
        "content_sha256": content_sha256(feed_id, raw.guid, raw.link, raw.title),
        "retrieved_at": retrieved_at or now_utc_z(),
        "display": display_fields(raw),
        "link_key": link_key(raw.link),
        "extra": dict(raw.extra),
    }


def label_words(row: dict) -> list[str]:
    """The ids a source gave as labels (agencies, change types) as words, so the injection guard reads
    them with the title and summary: they are published too."""
    return [v.replace("-", " ") for name in ("agencies", "change_types") for v in row.get(name) or [] if isinstance(v, str)]


__all__ = [
    "DISPLAY_SUMMARY_CAP", "EMAIL_REDACTION", "RawItem", "build_item", "content_sha256",
    "display_fields", "encodable", "encodable_item", "label_words", "link_key", "match_keywords",
    "normalize_link", "now_utc_z", "sanitize", "strip_html", "tag_topics", "to_utc_z",
]
