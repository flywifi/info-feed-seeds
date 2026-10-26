"""The reader-category registry (`schema/categories.json`): load it, check it, extend it, and
place sources and items in it.

The registry is used two ways, and the two are kept apart:

- The source category places a feed as a whole (`source_category` and `source_subcategory` in its
  manifest) and decides which sources a profile polls. A profile without `source_categories` polls
  every category whose `digest_default` is true; a category with `digest_default` false (machine
  data) is polled only by a profile that names it. `suggest()` proposes a source category from a
  feed's item titles and topics; a person confirms it.
- The content category places one item by its own title and summary (`content_category()`). A
  vendor advisory feed can carry an item about an attack under way; that item's content category
  says so while the feed's source category stays the same. When the text gives no signal the item
  takes the source category, and the result says which basis was used."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import ROOT, jsonio

CATEGORIES_FILE = ROOT / "schema" / "categories.json"
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Titles that are only a version number ("37.319") or a record id ("FR-2022-04-07") are written for programs.
VERSION_TITLE = re.compile(r"^v?\d+(?:\.\d+)+$")
RECORD_ID_TITLE = re.compile(r"^[A-Z]{2,6}(?:-[A-Z0-9]+)+$")
MACHINE_SHARE = 0.6


def load_categories(path: Path | None = None) -> dict:
    """The whole registry: description, schema_version, categories and industries."""
    return jsonio.load_json(path or CATEGORIES_FILE)


def duplicate_keys(path: Path) -> list[str]:
    """Object keys that appear twice in one object of the file (json.loads keeps only the last)."""
    found: list[str] = []

    def hook(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                found.append(key)
            seen.add(key)
        return dict(pairs)

    json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=hook)
    return found


def ordered(reg: dict) -> list[tuple[str, dict]]:
    return sorted(reg["categories"].items(), key=lambda kv: (kv[1]["order"], kv[0]))


def all_refs(reg: dict) -> set[str]:
    """Every valid reference: each category id and each category/subcategory pair."""
    refs = set()
    for cid, entry in reg["categories"].items():
        refs.add(cid)
        refs.update(f"{cid}/{sid}" for sid in entry["subcategories"])
    return refs


def is_valid_pair(reg: dict, category: str | None, subcategory: str | None) -> bool:
    entry = reg["categories"].get(category or "")
    return bool(entry) and (subcategory or "") in entry["subcategories"]


def digest_default(reg: dict, category: str | None) -> bool:
    """True when a profile without a categories list shows this category; an unknown category counts as shown."""
    entry = reg["categories"].get(category or "")
    return True if entry is None else bool(entry["digest_default"])


def default_on(reg: dict) -> list[str]:
    return [cid for cid, entry in ordered(reg) if entry["digest_default"]]


def industry_ids(reg: dict) -> set[str]:
    return set(reg.get("industries", {}))


def registry_problems(reg: dict, topic_tags: set[str] | None = None) -> list[str]:
    """What the schema cannot check: id shapes, unique orders, non-empty categories, valid topic hints."""
    problems = []
    orders: dict[int, str] = {}
    for cid, entry in sorted(reg["categories"].items()):
        if not ID_RE.match(cid):
            problems.append(f"category id {cid!r} is not lowercase kebab")
        if entry["order"] in orders:
            problems.append(f"categories {orders[entry['order']]!r} and {cid!r} share order {entry['order']}")
        orders[entry["order"]] = cid
        if not entry["subcategories"]:
            problems.append(f"category {cid!r} has no subcategories")
        sub_orders: dict[int, str] = {}
        for sid, sub in sorted(entry["subcategories"].items()):
            if not ID_RE.match(sid):
                problems.append(f"subcategory id {cid}/{sid!r} is not lowercase kebab")
            if sub["order"] in sub_orders:
                problems.append(f"subcategories {cid}/{sub_orders[sub['order']]} and {cid}/{sid} share order {sub['order']}")
            sub_orders[sub["order"]] = sid
            for tag in sub.get("topics", []):
                if topic_tags is not None and tag not in topic_tags:
                    problems.append(f"subcategory {cid}/{sid}: topic hint {tag!r} is not in schema/topics.json")
    for iid in reg.get("industries", {}):
        if not ID_RE.match(iid):
            problems.append(f"industry id {iid!r} is not lowercase kebab")
    return problems


def _word(phrase: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + r"[\s-]+".join(re.escape(w) for w in phrase.lower().split()) + r"(?!\w)")


def suggest(reg: dict, titles: list[str], source_topics: list[str]) -> list[dict]:
    """Ranked placements for a source: [{"ref", "score", "reasons"}], best first, empty when nothing fits.
    Machine data is proposed when most titles are bare version numbers or record ids; otherwise each
    subcategory scores 2 per matching topic hint and 1 per title that contains one of its keywords."""
    clean = [" ".join((t or "").split()) for t in titles if (t or "").strip()]
    if clean:
        versions = sum(1 for t in clean if VERSION_TITLE.match(t))
        records = sum(1 for t in clean if RECORD_ID_TITLE.match(t))
        for count, ref, what in ((versions, "machine-data/signature-versions", "version numbers"),
                                 (records, "machine-data/bulk-data", "record ids")):
            if count / len(clean) >= MACHINE_SHARE and ref in all_refs(reg):
                return [{"ref": ref, "score": count, "reasons": [f"{count} of {len(clean)} titles are {what}"]}]
    lowered = [t.lower() for t in clean]
    topics = set(source_topics or [])
    parents = {t.split("/", 1)[0] for t in topics}
    out = []
    for cid, entry in ordered(reg):
        for sid, sub in sorted(entry["subcategories"].items(), key=lambda kv: kv[1]["order"]):
            score, reasons = 0, []
            for hint in sub.get("topics", []):
                if hint in topics or ("/" not in hint and hint in parents):
                    score += 2
                    reasons.append(f"topic {hint}")
            for kw in sub.get("keywords", []):
                rx = _word(kw)
                hits = sum(1 for t in lowered if rx.search(t))
                if hits:
                    score += hits
                    reasons.append(f"{hits} title(s) with {kw!r}")
            if score:
                out.append({"ref": f"{cid}/{sid}", "score": score, "reasons": reasons,
                            "_order": (-score, entry["order"], sub["order"])})
    out.sort(key=lambda s: s.pop("_order"))
    return out


def content_category(reg: dict, title: str, summary: str, source_category: str | None,
                     source_subcategory: str | None) -> dict:
    """One item's content category and content subcategory, each decided on its own:
    {"category", "subcategory", "category_basis", "subcategory_basis", "reasons"}. A basis is "item"
    when the item's own text decided that level and "source" when it fell back to the source category
    or source subcategory. A version-number or record-id title decides both levels. Otherwise each
    keyword scores 2 in the title and 1 in the summary: the category with the highest total (2 or more)
    wins, then the subcategory with the highest score inside it (2 or more, or any score when the
    category is not the source category). Ties keep the source category and source subcategory."""
    text_title = " ".join((title or "").split())
    for rx, ref, what in ((VERSION_TITLE, "machine-data/signature-versions", "version number"),
                          (RECORD_ID_TITLE, "machine-data/bulk-data", "record id")):
        if text_title and rx.match(text_title) and ref in all_refs(reg):
            cid, sid = ref.split("/")
            return {"category": cid, "subcategory": sid, "category_basis": "item", "subcategory_basis": "item",
                    "reasons": [f"the title is a {what}"]}
    low_title, low_summary = text_title.lower(), " ".join((summary or "").split()).lower()
    scores: dict[str, dict[str, int]] = {}
    reasons: dict[tuple[str, str], list[str]] = {}
    for cid, entry in ordered(reg):
        for sid, sub in sorted(entry["subcategories"].items(), key=lambda kv: kv[1]["order"]):
            score, why = 0, []
            for kw in sub.get("keywords", []):
                rx = _word(kw)
                if rx.search(low_title):
                    score, why = score + 2, why + [f"title has {kw!r}"]
                elif rx.search(low_summary):
                    score, why = score + 1, why + [f"summary has {kw!r}"]
            scores.setdefault(cid, {})[sid] = score
            reasons[(cid, sid)] = why

    def pick(options: dict[str, int], preferred: str | None) -> tuple[str | None, int]:
        best, best_score = None, 0
        for key, score in options.items():   # options keep registry order
            if score > best_score or (score == best_score and score and key == preferred):
                best, best_score = key, score
        return best, best_score

    cat, cat_score = pick({cid: sum(subs.values()) for cid, subs in scores.items()}, source_category)
    if cat is not None and cat_score >= 2:
        category, category_basis = cat, "item"
    else:
        category, category_basis = source_category, "source"
    sub_scores = scores.get(category or "", {})
    sid, sid_score = pick(sub_scores, source_subcategory if category == source_category else None)
    if sid is not None and (sid_score >= 2 or (category != source_category and sid_score >= 1)):
        subcategory, subcategory_basis = sid, "item"
    elif category == source_category:
        subcategory, subcategory_basis = source_subcategory, "source"
    else:
        subcategory, subcategory_basis = sid, "item"
    why = reasons.get((category, subcategory), []) if "item" in (category_basis, subcategory_basis) else []
    return {"category": category, "subcategory": subcategory, "category_basis": category_basis,
            "subcategory_basis": subcategory_basis,
            "reasons": why or ["no signal in the item text; the source category and source subcategory"]}


def add_entry(reg: dict, ref: str, *, label: str, note: str, today: str, keywords: list[str] | None = None,
              category_label: str | None = None, purpose: str | None = None, reason: str | None = None,
              machine_data: bool = False) -> dict:
    """A copy of the registry with subcategory `category/subcategory` added. A new category also needs
    its label, its reader purpose and a reason; it takes the next order. Raises ValueError on a bad id,
    a duplicate or a missing field."""
    cid, slash, sid = ref.partition("/")
    if not slash or not sid:
        raise ValueError("give the new entry as category/subcategory")
    for part in (cid, sid):
        if not ID_RE.match(part):
            raise ValueError(f"{part!r} is not a lowercase kebab id")
    if not label.strip() or not note.strip():
        raise ValueError("--label and --note must not be empty")
    out = copy.deepcopy(reg)
    cats = out["categories"]
    if cid not in cats:
        if not (category_label and purpose and reason):
            raise ValueError(f"category {cid!r} is new: give --category-label, --purpose and --reason")
        cats[cid] = {"label": category_label, "order": max((c["order"] for c in cats.values()), default=0) + 1,
                     "reader_purpose": purpose, "digest_default": not machine_data, "added": today,
                     "reason": reason, "subcategories": {}}
    elif category_label or purpose or machine_data:
        raise ValueError(f"category {cid!r} exists; --category-label, --purpose and --machine-data apply only to a new one")
    subs = cats[cid]["subcategories"]
    if sid in subs:
        raise ValueError(f"{cid}/{sid} is already in the registry")
    subs[sid] = {"label": label, "order": max((s["order"] for s in subs.values()), default=0) + 1, "note": note,
                 "keywords": [k.strip() for k in (keywords or []) if k.strip()], "topics": []}
    return out


__all__ = ["CATEGORIES_FILE", "add_entry", "all_refs", "content_category", "default_on", "digest_default", "duplicate_keys",
           "industry_ids", "is_valid_pair", "load_categories", "ordered", "registry_problems", "suggest"]
