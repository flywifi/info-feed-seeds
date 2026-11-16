"""The eCFR change list: https://www.ecfr.gov/api/search/v1/results with no search word lists every
changed section and appendix, newest first. One item per change: its citation and heading as the title,
the current eCFR page as the link, the date the change started as its date, and the labels the eCFR
gives (the CFR part and section, and the change types). The body is read as data; nothing in it is
followed."""
from __future__ import annotations

import json
from urllib.parse import quote

from ..extract import cfr_ref
from ..items import RawItem, to_utc_z
from .base import AdapterError, iso_day, slugs

SITE = "https://www.ecfr.gov/current"


def _text(value) -> str:
    """One line of text; a character that cannot be encoded (a lone surrogate) becomes '?'."""
    return " ".join(value.split()).encode("utf-8", "replace").decode("utf-8") if isinstance(value, str) else ""


def parse(data: bytes, *, url: str | None = None, params: dict | None = None) -> dict:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AdapterError(f"not JSON: {exc}") from None
    rows = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise AdapterError("no results list: not an eCFR change list")
    items = []
    for row in rows:
        where = row.get("hierarchy") if isinstance(row, dict) else None
        if not isinstance(where, dict):
            continue
        names = row.get("headings") if isinstance(row.get("headings"), dict) else {}
        title, part = where.get("title"), where.get("part")
        section, appendix = _text(where.get("section")), _text(where.get("appendix"))
        part_ref = cfr_ref(title, part)
        if not part_ref:
            continue
        number, part = part_ref.split("/")
        day = iso_day(row.get("starts_on"))
        refs = [part_ref]
        if section:
            cite, heading = f"{number} CFR {section}", _text(names.get("section"))
            link = f"{SITE}/title-{number}/section-{quote(section, safe='.-()')}"
            section_ref = cfr_ref(number, part, section)
            if section_ref:
                refs.append(section_ref)
        else:
            cite = f"{number} CFR part {part}" + (f", {appendix}" if appendix else "")
            heading = _text(names.get("appendix") if appendix else names.get("part"))
            link = f"{SITE}/title-{number}/part-{part}"
        extra: dict = {"cfr": refs}
        kinds = slugs(row.get("change_types"))
        if kinds:
            extra["change_types"] = kinds
        summary = "; ".join(t for t in (_text(names.get("part")), _text(names.get("chapter"))) if t)
        # The part is in the id: several parts of one title can carry an appendix of the same name.
        items.append(RawItem(guid=f"ecfr:{number}:{part}:{section or appendix or 'part'}:{day or 'undated'}",
                             link=link, title=f"{cite} {heading}".strip(), summary=summary,
                             published=to_utc_z(day), extra=extra))
    return {"kind": "ecfr", "items": items, "follow": []}


__all__ = ["parse"]
