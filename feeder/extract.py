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
# A severity word with "not" or "no" at most two words before it in the same clause states no
# severity ("not rated critical", "no critical-severity flaws"). "not only" and a "no" about user
# interaction, authentication, credentials, privileges, a patch, a fix, a workaround, action or a
# number ("no user interaction required (Critical)", "Advisory No 12") do not negate it.
_SEVERITY_NEGATED = re.compile(r"\b(?:not(?!\s+only\b)|no(?![\s-]*(?:\d|(?:user|auth\w*|credentials|privileges|patch|fix|"
                               r"workaround|action)\b)))\b(?:\W+\w+){0,2}\W*\Z", re.I | re.A)
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
        # Searched in the full text with pos and endpos, so a word boundary sees the character before the
        # 40-character window and the end of "piano" is not read as "no".
        start = max(0, m.start() - 40)
        cut = max((b.end() for b in _CLAUSE_END.finditer(text, start, m.start())), default=start)
        if _SEVERITY_NEGATED.search(text, cut, m.start()):
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


def _registry_entries(path: Path, key: str = "vendors"):
    """(problems, {id: entry}) from a name registry file (vendors or standards); the file may be missing."""
    if not path.exists():
        return [], {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"not valid JSON ({exc})"], {}
    entries = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(entries, dict):
        return [f"has no \"{key}\" object"], {}
    return [], entries


def load_vendors(path: Path, key: str = "vendors") -> dict[str, list[str]]:
    """{id: [aliases]} from the vendor registry (or, with key "standards", the standards registry), sorted
    by id. Entries and aliases the matcher cannot use (not an object, aliases not a list of strings, short
    or padded aliases) are skipped, and a missing or unreadable file gives none."""
    _, entries = _registry_entries(path, key)
    out = {}
    for vid, entry in sorted(entries.items()):
        aliases = entry.get("aliases") if isinstance(entry, dict) else None
        if isinstance(aliases, list):
            kept = [a for a in aliases if isinstance(a, str) and len(a.strip()) >= 2 and a == a.strip()]
            if kept:
                out[vid] = kept
    return out


def load_standards(path: Path) -> dict[str, list[str]]:
    """{standard id: [aliases]} from schema/standards.json, with the same rules as the vendor registry."""
    return load_vendors(path, "standards")


def standard_problems(path: Path) -> list[str]:
    """The vendor registry's checks, applied to schema/standards.json."""
    return vendor_problems(path, "standards", "standard", right=_STD_RIGHT)


def vendor_problems(path: Path, key: str = "vendors", noun: str = "vendor", right: str = r"(?!\w)") -> list[str]:
    """Registry defects lint reports: invalid JSON, a bad id, an entry that is not an object, aliases that
    are not a list of strings, an entry without aliases, an alias shorter than 2 characters or padded with
    spaces, an alias listed twice, one alias claimed by two entries, or an alias that sits inside another
    entry's longer alias and is hidden by it."""
    problems, entries = _registry_entries(path, key)
    owner: dict[str, str] = {}
    for vid, entry in entries.items():
        if not ID_RE.fullmatch(vid):
            problems.append(f"{noun} id {vid!r} is not lower-case words joined by hyphens")
        if not isinstance(entry, dict):
            problems.append(f"{noun} {vid!r} is not an object")
            continue
        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            problems.append(f"{noun} {vid!r}: aliases must be a list of strings")
            continue
        if not aliases:
            problems.append(f"{noun} {vid!r} has no aliases")
        seen: set[str] = set()
        for alias in aliases:
            if alias != alias.strip():
                problems.append(f"{noun} {vid!r}: alias {alias!r} has leading or trailing spaces")
            elif len(alias) < 2:
                problems.append(f"{noun} {vid!r}: alias {alias!r} is too short")
            elif alias in seen:
                problems.append(f"{noun} {vid!r}: alias {alias!r} is listed twice")
            elif alias in owner and owner[alias] != vid:
                problems.append(f"alias {alias!r} is claimed by {owner[alias]!r} and {vid!r}")
            seen.add(alias)
            owner.setdefault(alias, vid)
    for a, va in owner.items():
        for b, vb in owner.items():
            # The same right edge as the matcher, so an alias the matcher would hide is reported.
            if va != vb and a != b and re.search(r"(?<![\w-])" + re.escape(b) + right, a):
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


# Standards: an alias matches as a whole word with its exact capitalisation, like a vendor name. A
# revision written straight after it ("SP 800-53r5") still lets the name match.
_STD_RIGHT = r"(?!(?!r\d)\w)"
# The edition written right after a standard's name, by family: (pattern anchored at the end of the
# name, how the edition is stored). Nothing else in the text is read as an edition.
_VERSION = r"(\d{1,2}(?:\.\d{1,2}){1,2})(?!\w|\.\d)"
EDITION_AFTER = {
    "nist-sp-800-": (re.compile(r"(?: ?(?:Rev|Revision)\.? ?|r)(\d{1,2})(?!\w|\.\d)", re.A), "r{}"),
    "fips-140": (re.compile(r"-(\d)(?!\w|-\d)", re.A), "{}"),
    "iso-27001": (re.compile(r":(\d{4})(?!\w)", re.A), "{}"),
    "pci-dss": (re.compile(r" v?" + _VERSION, re.A), "{}"),
    # A CIS Controls edition needs v or Version: "CIS Controls 18" or "1-6" are control numbers.
    "cis-controls": (re.compile(r" (?:v|Version )(\d{1,2}(?:\.\d{1,2}){0,2})(?![\w-]|\.\d)", re.A), "{}"),
    "cmmc": (re.compile(r" v?" + _VERSION, re.A), "{}"),
    "nist-csf": (re.compile(r" v?" + _VERSION, re.A), "{}"),
}
# Editions read for one item; the rest of a long text adds none.
EDITION_CAP = 20
_ALIAS_EDITION = re.compile(r"\s(\d{1,2}(?:\.\d{1,2}){1,2})\Z", re.A)   # an alias that names its edition ("CSF 2.0")


def _standard_hits(text: str, names: dict[str, list[str]]):
    owner = {a: sid for sid, aliases in names.items() for a in aliases}
    if not owner or not text:
        return owner, iter(())
    pattern = (r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in sorted(owner, key=len, reverse=True)) + r")"
               + _STD_RIGHT)
    return owner, re.finditer(pattern, text, re.A)


def standards(text: str, names: dict[str, list[str]]) -> list[str]:
    """Standard ids whose aliases appear as whole words with their exact capitalisation, in order of
    first mention, each once, in one pass."""
    owner, hits = _standard_hits(text, names)
    out: list[str] = []
    for m in hits:
        sid = owner[m.group(0)]
        if sid not in out:
            out.append(sid)
    return out


def editions(text: str, names: dict[str, list[str]]) -> list[str]:
    """For each standard found, the edition written right after its name, as "<id>:<edition>" in order of
    first mention: "SP 800-53 Rev. 5" gives nist-sp-800-53:r5, "FIPS 140-3" gives fips-140:3,
    "ISO/IEC 27001:2022" gives iso-27001:2022, "PCI DSS v4.0.1" gives pci-dss:4.0.1, "CIS Controls v8"
    gives cis-controls:8, "CMMC 2.0" gives cmmc:2.0 and "CSF 2.0" gives nist-csf:2.0. A version anywhere
    else, or after another standard's name, is never an edition."""
    owner, hits = _standard_hits(text, names)
    out: dict[str, None] = {}   # insertion-ordered, so dedup stays linear in the number of editions
    for m in hits:
        sid, value = owner[m.group(0)], None
        own = _ALIAS_EDITION.search(m.group(0))
        if own:
            # An alias that names its edition counts only when no further version part follows ("CSF 2.0.1").
            value = own.group(1) if re.match(r"\.\d", text[m.end():m.end() + 2]) is None else None
        else:
            rule = next((r for prefix, r in EDITION_AFTER.items() if sid == prefix or
                         (prefix.endswith("-") and sid.startswith(prefix))), None)
            if rule is not None:
                after = rule[0].match(text, m.end())
                value = rule[1].format(after.group(1)) if after else None
        if value:
            out.setdefault(f"{sid}:{value}", None)
    return list(out)


def tags(title: str, summary: str, names: dict[str, list[str]],
         standard_names: dict[str, list[str]] | None = None) -> dict:
    """The store's filter columns for one item, from its title and summary."""
    text = f"{title or ''}\n{summary or ''}"
    sev = severity(text)
    found = deadlines(text)
    return {"cves": cve_ids(text), "severity": sev, "severity_rank": SEVERITY_RANK[sev] if sev else None,
            "exploited": exploited(text), "vendors": vendors(text, names), "deadline": found[0] if found else None,
            "standards": standards(text, standard_names or {}),
            "editions": editions(text, standard_names or {})[:EDITION_CAP]}


__all__ = ["EDITION_AFTER", "EDITION_CAP", "SEVERITY_RANK", "cve_ids", "deadlines", "editions", "exploited", "load_standards",
           "load_vendors", "severity", "severity_from_score", "standard_problems", "standards", "tags",
           "vendor_problems", "vendors"]
