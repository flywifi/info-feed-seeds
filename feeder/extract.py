"""Tags read from an item's own text at ingest: CVE ids, severity, exploitation, vendors and deadlines.

Every reader takes the sanitized title and summary (HTML stripped, dashes folded) and returns only
what the text states: a severity is a severity word in a severity context or a CVSS score, never a
guess, and a field the text does not support stays empty. Vendors come from `schema/vendors.json`,
matched as whole words with their exact capitalisation, so "IOS" (Cisco) and "iOS" (Apple) stay
apart."""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from . import jsonio

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,19}\b", re.I)
SEVERITY_WORDS = ("none", "low", "medium", "high", "critical")
SEVERITY_RANK = {w: i for i, w in enumerate(SEVERITY_WORDS)}
_WORD = r"(critical|high|medium|low)"
SEVERITY_RE = re.compile(rf"\({_WORD}\)|\b{_WORD}[ -]severity\b|\bseverity(?: level| rating)?\s*[:=]?\s*{_WORD}\b"
                         rf"|\brated {_WORD}\b", re.I)
CVSS_RE = re.compile(r"\bCVSS(?:\s*v?[234](?:\.\d)?)?(?:\s*base)?(?:\s*score)?\s*(?:of\s*)?[:=]?\s*(\d{1,2}(?:\.\d)?)(?!\.?\d)",
                     re.I)
EXPLOITED_RE = re.compile(r"\b(actively exploited|exploited in the wild|known exploited|under active exploitation|"
                          r"exploitation (?:has been )?(?:observed|detected|reported)|active zero[ -]day|"
                          r"zero[ -]day (?:is |was |being )?exploited)\b", re.I)
_DATE = r"(\d{1,2}\s+[A-Za-z]+\s+20\d{2}|[A-Za-z]+\s+\d{1,2},\s+20\d{2}|20\d{2}-\d{2}-\d{2})"
DEADLINE_RE = re.compile(r"\b(?:comments?\s+(?:are\s+)?due|comment\s+period\s+(?:ends|closes)|deadline|due\s+date|"
                         r"effective(?:\s+date)?|must\s+be\s+(?:applied|implemented|remediated)(?:\s+by)?)\b[^.]{0,80}?\b"
                         + _DATE, re.I)
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def cve_ids(text: str) -> list[str]:
    """CVE ids in order of first mention, upper case, each once."""
    out: list[str] = []
    for m in CVE_RE.finditer(text or ""):
        value = m.group(0).upper()
        if value not in out:
            out.append(value)
    return out


def severity_from_score(score: float) -> str | None:
    """CVSS v3 and v4 bands: 0.0 none, 0.1 to 3.9 low, 4.0 to 6.9 medium, 7.0 to 8.9 high, 9.0 to 10.0 critical."""
    if score < 0 or score > 10:
        return None
    if score == 0:
        return "none"
    return "low" if score < 4 else "medium" if score < 7 else "high" if score < 9 else "critical"


def severity(text: str) -> str | None:
    """The highest severity the text states, as a severity word in a severity context or a CVSS score."""
    best = None
    found = [next(g for g in m.groups() if g).lower() for m in SEVERITY_RE.finditer(text or "")]
    found += [w for w in (severity_from_score(float(m.group(1))) for m in CVSS_RE.finditer(text or "")) if w]
    for word in found:
        if best is None or SEVERITY_RANK[word] > SEVERITY_RANK[best]:
            best = word
    return best


def exploited(text: str) -> bool:
    return bool(EXPLOITED_RE.search(text or ""))


def _long_date(value: str) -> str | None:
    """'March 31, 2027', '1 July 2027' or an ISO date as YYYY-MM-DD; an impossible date or a month name
    that is not English is None, never a guess."""
    value = value.strip()
    try:
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
            return datetime.date.fromisoformat(value).isoformat()
        m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", value) or re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s+(20\d{2})", value)
        if not m:
            return None
        a, b, year = m.groups()
        day, month = (a, b) if a.isdigit() else (b, a)
        if month.lower() not in MONTHS:
            return None
        return datetime.date(int(year), MONTHS[month.lower()], int(day)).isoformat()
    except ValueError:
        return None


def deadlines(text: str) -> list[str]:
    """Dates the text ties to a deadline, a comment period or an effective date, in order, each once."""
    out: list[str] = []
    for m in DEADLINE_RE.finditer(text or ""):
        date = _long_date(m.group(1))
        if date and date not in out:
            out.append(date)
    return out


def load_vendors(path: Path) -> dict[str, list[str]]:
    """{vendor id: [aliases]} from the vendor registry, in registry order; empty when the file is missing."""
    if not path.exists():
        return {}
    doc = jsonio.load_json(path)
    return {vid: list(v.get("aliases") or []) for vid, v in sorted((doc.get("vendors") or {}).items(),
                                                                       key=lambda kv: kv[0])}


def vendor_problems(path: Path) -> list[str]:
    """Registry defects lint reports: a bad id, a vendor without aliases, an alias shorter than 2
    characters, or one alias claimed by two vendors."""
    if not path.exists():
        return []
    problems, owner = [], {}
    for vid, entry in (jsonio.load_json(path).get("vendors") or {}).items():
        if not ID_RE.match(vid):
            problems.append(f"vendor id {vid!r} is not lower-case words joined by hyphens")
        aliases = entry.get("aliases") or []
        if not aliases:
            problems.append(f"vendor {vid!r} has no aliases")
        for alias in aliases:
            if len(alias.strip()) < 2:
                problems.append(f"vendor {vid!r}: alias {alias!r} is too short")
            elif alias in owner and owner[alias] != vid:
                problems.append(f"alias {alias!r} is claimed by {owner[alias]!r} and {vid!r}")
            owner.setdefault(alias, vid)
    return problems


def vendors(text: str, names: dict[str, list[str]]) -> list[str]:
    """Vendor ids whose aliases appear as whole words, with their exact capitalisation."""
    return [vid for vid, aliases in names.items()
            if any(re.search(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])", text or "") for a in aliases)]


def tags(title: str, summary: str, names: dict[str, list[str]]) -> dict:
    """The store's filter columns for one item, from its title and summary."""
    text = f"{title or ''}\n{summary or ''}"
    sev = severity(text)
    found = deadlines(text)
    return {"cves": cve_ids(text), "severity": sev, "severity_rank": SEVERITY_RANK[sev] if sev else None,
            "exploited": exploited(text), "vendors": vendors(text, names), "deadline": found[0] if found else None}


__all__ = ["SEVERITY_RANK", "cve_ids", "deadlines", "exploited", "load_vendors", "severity", "severity_from_score",
           "tags", "vendor_problems", "vendors"]
