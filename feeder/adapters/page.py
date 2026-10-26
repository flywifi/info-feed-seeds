"""Page adapter: items from an HTML listing page (a blog index, a changelog) that has no feed.

The route's params name the parts of the page in a small CSS subset. `item_selector` picks one node per
item; `title_selector`, `link_selector`, `date_selector` and `summary_selector` read inside that node.
A selector is one or more groups separated by commas; a group is steps separated by spaces (descendant),
and a step is `tag`, `#id`, `.class` or a combination such as `div#main.a.b`. `sel@attr` reads an
attribute (`href` and `src` resolved against the page URL), `sel` reads text, and `.` is the item node
itself. `next_selector` (for example `a.next@href`) names the link to the next, older page; the
poller follows it up to `max_pages` and stops at the first page holding nothing new. Anything outside the subset (`>`, `[attr]`, `:first-child`) is refused before parsing.

Dates: `date_regex` picks the date out of the selected text, `date_format` is a strptime format, and
without a format only ISO 8601 parses. Month names in the format (%b, %B) are read from a fixed English
table, never from the machine's locale. A date without a zone is UTC; a date that does not parse is
None, never a guess. Script and style text is never read."""
from __future__ import annotations

import datetime
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..items import RawItem
from .base import AdapterError


class PageParseError(AdapterError):
    """The route's selectors or date pattern cannot be used, or the route has no item_selector."""


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP = ("script", "style")
STEP = re.compile(r"^([a-z][a-z0-9]*)?(?:#([\w-]+))?((?:\.[\w-]+)*)$")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")


class Node:
    __slots__ = ("tag", "attrs", "parts", "parent")

    def __init__(self, tag: str, attrs, parent: "Node | None") -> None:
        self.tag, self.attrs, self.parts, self.parent = tag, dict(attrs), [], parent

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(p if isinstance(p, str) else p.text() for p in self.parts)).strip()

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def walk(self):
        for p in self.parts:
            if isinstance(p, Node):
                yield p
                yield from p.walk()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", [], None)
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        node = Node(tag, attrs, self.cur)
        self.cur.parts.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag not in SKIP:
            self.cur.parts.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:   # an end tag with no open element is ignored
            self.cur = node.parent

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.cur.parts.append(data)


def parse_html(text: str) -> Node:
    tree = _Tree()
    tree.feed(text)
    tree.close()
    return tree.root


def compile_selector(selector: str) -> list[list[tuple]]:
    """Comma-separated groups of descendant steps; each step is (tag, id, [classes])."""
    groups = []
    for group in (selector or "").split(","):
        steps = []
        for step in group.split():
            m = STEP.match(step)
            if not m or not any(m.groups()):
                raise PageParseError(f"unsupported selector step {step!r} in {selector!r}")
            tag, ident, cls = m.groups()
            steps.append((tag, ident, [c for c in cls.split(".") if c]))
        if not steps:
            raise PageParseError(f"empty selector group in {selector!r}")
        groups.append(steps)
    return groups


def _match(node: Node, step: tuple) -> bool:
    tag, ident, cls = step
    if tag and node.tag != tag:
        return False
    if ident and node.attrs.get("id") != ident:
        return False
    return all(c in node.classes() for c in cls)


def select(root: Node, groups: list[list[tuple]]) -> list[Node]:
    """Nodes under `root` matching any group, once each, in document order."""
    hits: set[int] = set()
    for steps in groups:
        found = [root]
        for step in steps:
            found = [n for parent in found for n in parent.walk() if _match(n, step)]
        hits.update(id(n) for n in found)
    return [n for n in root.walk() if id(n) in hits]


def compile_value(selector: str | None):
    """(groups or None for the item node itself, attribute or None), or None when no selector is set."""
    if not selector:
        return None
    sel, _, attr = selector.partition("@")
    return (None if sel.strip() in ("", ".") else compile_selector(sel), attr.strip() or None)


def first_value(node: Node, compiled, base: str) -> str | None:
    if compiled is None:
        return None
    groups, attr = compiled
    if groups is None:
        target = node
    else:
        found = select(node, groups)
        target = found[0] if found else None
    if target is None:
        return None
    if attr:
        value = (target.attrs.get(attr) or "").strip()
        if not value:
            return None
        return urljoin(base, value) if attr in ("href", "src") else value
    return target.text() or None


def _english_months(text: str, fmt: str) -> tuple[str | None, str]:
    """Swap %b/%B for %m and the English month name in the text for its number, so parsing never
    depends on the locale. A month word that is not English makes the date unparseable (None)."""
    if "%b" not in fmt and "%B" not in fmt:
        return text, fmt
    m = re.search(r"[A-Za-z]{3,}", text)
    if not m:
        return text, fmt
    word = m.group(0).lower()
    month = next((i for i, name in enumerate(MONTHS, 1) if word in (name, name[:3])), None)
    if month is None:
        return None, fmt
    return text[:m.start()] + f"{month:02d}" + text[m.end():], fmt.replace("%b", "%m").replace("%B", "%m")


def parse_date(text: str | None, date_format: str | None = None, date_regex: str | None = None) -> str | None:
    """'YYYY-MM-DDTHH:MM:SSZ' from the text, or None when nothing parses."""
    if not text:
        return None
    if date_regex:
        m = re.search(date_regex, text)
        if not m:
            return None
        text = m.group(0)
    text = text.strip()
    try:
        if date_format:
            text, date_format = _english_months(text, date_format)
            if text is None:
                return None
            dt = datetime.datetime.strptime(text, date_format)
        else:
            dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(data: bytes | str, *, url: str | None = None, params: dict | None = None) -> dict:
    """One item per item_selector match: title (default `a`), link (default `a@href`), date and summary
    from the sub-selectors. A match with neither title nor link is skipped; a link-less entry is keyed
    by its title."""
    params = params or {}
    base = url or ""
    if not params.get("item_selector"):
        raise PageParseError("page route has no item_selector")
    items_sel = compile_selector(params["item_selector"])
    title_c = compile_value(params.get("title_selector") or "a")
    link_c = compile_value(params.get("link_selector") or "a@href")
    date_c = compile_value(params.get("date_selector"))
    summary_c = compile_value(params.get("summary_selector"))
    next_c = compile_value(params.get("next_selector"))
    date_regex = params.get("date_regex")
    if date_regex:
        try:
            re.compile(date_regex)
        except re.error as exc:
            raise PageParseError(f"bad date_regex: {exc}") from None
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    root = parse_html(text)
    items = []
    for node in select(root, items_sel):
        title, link = first_value(node, title_c, base), first_value(node, link_c, base)
        if not title and not link:
            continue
        published = parse_date(first_value(node, date_c, base), params.get("date_format"), date_regex)
        items.append(RawItem(guid=link or title, link=link, title=title or link,
                             summary=first_value(node, summary_c, base) or "", published=published))
    nxt = first_value(root, next_c, base) if next_c else None
    return {"kind": "page", "items": items, "follow": [nxt] if nxt else []}


__all__ = ["PageParseError", "compile_selector", "parse", "parse_date", "parse_html", "select"]
