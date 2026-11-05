"""Tags read from an item's own text at ingest: CVE ids, severity, exploitation, vendors and deadlines.

Every reader takes the sanitized title and summary (HTML stripped, dashes folded) and returns only
what the text states: a severity is a severity word in a severity context or a CVSS score (never a
CVSS version or vector), exploitation is a stated exploitation phrase that no negation or hedge in
the same clause qualifies, and a field the text does not support stays empty. Vendors come from
`schema/vendors.json`, matched as whole words with their exact capitalisation, so "IOS" (Cisco) and
"iOS" (Apple) stay apart. Every pattern is ASCII-only and linear in the text."""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from . import jsonio

CVE_RE = re.compile(r"\bCVE-\d{4}-(?:\d{4}|[1-9]\d{4,18})\b", re.I | re.A)
SEVERITY_WORDS = ("none", "low", "medium", "high", "critical")
SEVERITY_RANK = {w: i for i, w in enumerate(SEVERITY_WORDS)}
_WORD = r"(critical|high|important|medium|moderate|low)"
# Vendor scales that use other words for the same bands (Microsoft "Important", Red Hat "Moderate").
_SCALE = {"important": "high", "moderate": "medium"}
SEVERITY_RE = re.compile(rf"\({_WORD}\)|\b{_WORD}[ -]severity\b|\bseverity(?: level| rating)?\s?[:=]?\s?{_WORD}\b"
                         rf"|\brated {_WORD}(?![\w-])", re.I | re.A)
CVSS_RE = re.compile(r"\bCVSS(?P<ver>(?::\s?|\s?v?)[234](?:\.\d)?)?(?P<kw>(?:\s?base)?(?:\s?score)?(?:\s?of)?\s?[:=]?)\s?"
                     r"(?P<score>\d{1,2}(?:\.\d)?)(?!\.?\d|/)", re.I | re.A)
_CVSS_VERSIONS = {"2.0", "3.0", "3.1", "4.0"}
EXPLOITED_RE = re.compile(r"\b(actively exploited|exploited in the wild|known exploited|under active exploitation|"
                          r"active exploitation|exploitation (?:has been )?(?:observed|detected|reported)|detected exploitation|"
                          r"active zero[ -]day|zero[ -]day (?:is |was |being )?exploited|zero[ -]day exploitation|"
                          r"exploited as a zero[ -]day|exploit (?:for \S+ )?exists in the wild|may have been exploited)\b",
                          re.I | re.A)
# A negation or hedge earlier in the same clause: "has not been exploited in the wild", "could be
# actively exploited". An exploitation phrase after one stays empty.
_CLAUSE_END = re.compile(r"[.;:!?\n]|,\s(?:and|but|yet|while|whereas)\b|\bbut\b", re.I | re.A)
_NEGATED = re.compile(r"\b(?:not(?!\sonly\b)|no(?!\s(?:longer|user\sinteraction|authentication|credentials|privileges|patch|fix|"
                      r"workaround)\b)|never|nor|cannot|unlikely|could|might|may(?!\shave\sbeen)|would)\b", re.I | re.A)
# A severity word after "not" or "no" in the same clause states no severity.
_SEVERITY_NEGATED = re.compile(r"\b(?:not|no)\b", re.I | re.A)
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
_MONTH_TOKENS = {**MONTHS, **{m[:3]: i for m, i in MONTHS.items()}, "sept": 9}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
# A date: "5 December 2026", "Dec. 5th, 2026", or an ISO date, which may run on into a time (2026-11-24T00:00Z).
_DATE = (rf"(\d{{1,2}}(?:st|nd|rd|th)?\s{_MON}\.?,?\s20\d{{2}}(?![\w-])|{_MON}\.?\s\d{{1,2}}(?:st|nd|rd|th)?,?\s20\d{{2}}(?![\w-])"
         r"|20\d{2}-\d{2}-\d{2}(?:(?=T\d)|(?![\w-])))")
# Between the deadline words and the date: no sentence end, and a comma only before a number
# ("version 2, 2027 builds"), so "The deadline, announced on October 1" never takes that date.
# A gap never crosses "after" or "following", so "takes effect 30 days after publication on <date>" is not
# read as a deadline on the publication date; "11:59 p.m." may sit in it.
_GAP = r"(?:(?!(?<![\w-])(?:after|following)(?![\w-]))(?:[ap]\.m\.|[^.,\n]|,(?=\s?\d))){0,40}?"
_WEEKDAY = r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s)?"
DEADLINE_RE = re.compile(r"(?<![\w-])(?:comments?\s(?:are\s)?due|comments?\smust\sbe\s(?:received|submitted)|submit\scomments|"
                         r"comment\speriod\s(?:ends|closes)|deadline|due\sdate|no\slater\sthan|takes?\seffect|effective\sdate|"
                         r"must\sbe\s(?:applied|implemented|remediated)|must\s(?:remediate|patch|apply)|"
                         r"(?:required|requires|ordered|directs?)\s(?:[a-z-]+\s){0,4}?to\s(?:remediate|patch|apply))\b"
                         + _GAP + r"(?<![\w-])" + _WEEKDAY + _DATE, re.I | re.A)
EFFECTIVE_RE = re.compile(r"(?<![\w-])effective(?:\sdate)?(?:\s?:|\s(?:on|as\sof|from))?\s" + _WEEKDAY + _DATE, re.I | re.A)
ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.A)


def cve_ids(text: str) -> list[str]:
    """CVE ids in order of first mention, upper case, each once."""
    return list(dict.fromkeys(m.group(0).upper() for m in CVE_RE.finditer(text or "")))


def severity_from_score(score: float) -> str | None:
    """CVSS v3 and v4 bands: 0.0 none, 0.1 to 3.9 low, 4.0 to 6.9 medium, 7.0 to 8.9 high, 9.0 to 10.0 critical."""
    if score < 0 or score > 10:
        return None
    if score == 0:
        return "none"
    return "low" if score < 4 else "medium" if score < 7 else "high" if score < 9 else "critical"


def _cvss_words(text: str) -> list[str]:
    """Severity words from CVSS scores. A v2 score is skipped (its bands differ), and a bare version
    number ("CVSS 3.1", "CVSS:4.0/AV:N") is not a score."""
    out = []
    for m in CVSS_RE.finditer(text):
        ver, kw = re.sub(r"[^\d.]", "", m.group("ver") or ""), m.group("kw").strip()
        if ver.startswith("2") or (not ver and m.group("score") in _CVSS_VERSIONS
                                   and (not kw or (kw in ":=" and not re.search(r"\s", m.group(0))))):
            continue
        word = severity_from_score(float(m.group("score")))
        if word:
            out.append(word)
    return out


def severity(text: str) -> str | None:
    """The highest severity the text states, as a severity word in a severity context or a CVSS score."""
    best = None
    text = text or ""
    found = []
    for m in SEVERITY_RE.finditer(text):
        window = text[max(0, m.start() - 80):m.start()]
        clause = window[max((b.end() for b in _CLAUSE_END.finditer(window)), default=0):]
        if _SEVERITY_NEGATED.search(clause):
            continue   # "not rated critical", "no high-severity issues"
        word = next(g for g in m.groups() if g).lower()
        found.append(_SCALE.get(word, word))
    found += _cvss_words(text)
    for word in found:
        if best is None or SEVERITY_RANK[word] > SEVERITY_RANK[best]:
            best = word
    return best


def exploited(text: str) -> bool:
    """True when the text states exploitation and no negation or hedge in the same clause qualifies it."""
    text = text or ""
    for m in EXPLOITED_RE.finditer(text):
        window = text[max(0, m.start() - 80):m.start()]
        clause = window[max((b.end() for b in _CLAUSE_END.finditer(window)), default=0):]
        if not _NEGATED.search(clause):
            return True
    return False


def _long_date(value: str) -> str | None:
    """'March 31, 2027', 'Nov. 10, 2026', '1st July 2027' or an ISO date as YYYY-MM-DD; an impossible date
    or a month name that is not English is None, never a guess."""
    value = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b|[.,]", "", value.strip(), flags=re.I)
    try:
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value, re.A):
            return datetime.date.fromisoformat(value).isoformat()
        a, b, year = value.split()
        day, month = (a, b) if a.isdigit() else (b, a)
        return datetime.date(int(year), _MONTH_TOKENS[month.lower()], int(day)).isoformat()
    except (ValueError, KeyError):
        return None


def deadlines(text: str) -> list[str]:
    """Dates the text ties to a deadline, a comment period, a remediation requirement or an effective
    date, in order, each once."""
    text = text or ""
    matches = sorted([*DEADLINE_RE.finditer(text), *EFFECTIVE_RE.finditer(text)], key=lambda m: m.start())
    return list(dict.fromkeys(d for d in (_long_date(m.group(1)) for m in matches) if d))


def _vendor_entries(path: Path):
    """(problems, {vendor id: entry}) from the registry file; the file may be missing."""
    if not path.exists():
        return [], {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"not valid JSON ({exc})"], {}
    vendors = doc.get("vendors") if isinstance(doc, dict) else None
    if not isinstance(vendors, dict):
        return ["has no \"vendors\" object"], {}
    return [], vendors


def load_vendors(path: Path) -> dict[str, list[str]]:
    """{vendor id: [aliases]} from the vendor registry, sorted by vendor id. Entries and aliases the matcher
    cannot use (not an object, aliases not a list of strings, short or padded aliases) are skipped, and a
    missing or unreadable file gives none."""
    _, entries = _vendor_entries(path)
    out = {}
    for vid, entry in sorted(entries.items()):
        aliases = entry.get("aliases") if isinstance(entry, dict) else None
        if isinstance(aliases, list):
            kept = [a for a in aliases if isinstance(a, str) and len(a.strip()) >= 2 and a == a.strip()]
            if kept:
                out[vid] = kept
    return out


def vendor_problems(path: Path) -> list[str]:
    """Registry defects lint reports: invalid JSON, a bad id, an entry that is not an object, aliases that
    are not a list of strings, a vendor without aliases, an alias shorter than 2 characters or padded with
    spaces, an alias listed twice, one alias claimed by two vendors, or an alias that sits inside another
    vendor's longer alias and is hidden by it."""
    problems, entries = _vendor_entries(path)
    owner: dict[str, str] = {}
    for vid, entry in entries.items():
        if not ID_RE.fullmatch(vid):
            problems.append(f"vendor id {vid!r} is not lower-case words joined by hyphens")
        if not isinstance(entry, dict):
            problems.append(f"vendor {vid!r} is not an object")
            continue
        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            problems.append(f"vendor {vid!r}: aliases must be a list of strings")
            continue
        if not aliases:
            problems.append(f"vendor {vid!r} has no aliases")
        seen: set[str] = set()
        for alias in aliases:
            if alias != alias.strip():
                problems.append(f"vendor {vid!r}: alias {alias!r} has leading or trailing spaces")
            elif len(alias) < 2:
                problems.append(f"vendor {vid!r}: alias {alias!r} is too short")
            elif alias in seen:
                problems.append(f"vendor {vid!r}: alias {alias!r} is listed twice")
            elif alias in owner and owner[alias] != vid:
                problems.append(f"alias {alias!r} is claimed by {owner[alias]!r} and {vid!r}")
            seen.add(alias)
            owner.setdefault(alias, vid)
    for a, va in owner.items():
        for b, vb in owner.items():
            if va != vb and a != b and re.search(r"(?<![\w-])" + re.escape(b) + r"(?![\w-])", a):
                problems.append(f"alias {b!r} ({vb}) is inside alias {a!r} ({va}) and is hidden by it")
    return problems


def vendors(text: str, names: dict[str, list[str]]) -> list[str]:
    """Vendor ids whose aliases appear as whole words, with their exact capitalisation, in one pass."""
    owner = {a: vid for vid, aliases in names.items() for a in aliases}
    if not owner or not text:
        return []
    # A hyphen after the name still names the vendor ("Microsoft-signed"); one before it does not ("non-Microsoft").
    pattern = r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in sorted(owner, key=len, reverse=True)) + r")(?!\w)"
    found = {owner[m.group(0)] for m in re.finditer(pattern, text, re.A)}
    return [vid for vid in names if vid in found]


def tags(title: str, summary: str, names: dict[str, list[str]]) -> dict:
    """The store's filter columns for one item, from its title and summary."""
    text = f"{title or ''}\n{summary or ''}"
    sev = severity(text)
    found = deadlines(text)
    return {"cves": cve_ids(text), "severity": sev, "severity_rank": SEVERITY_RANK[sev] if sev else None,
            "exploited": exploited(text), "vendors": vendors(text, names), "deadline": found[0] if found else None}


__all__ = ["SEVERITY_RANK", "cve_ids", "deadlines", "exploited", "load_vendors", "severity", "severity_from_score",
           "tags", "vendor_problems", "vendors"]
