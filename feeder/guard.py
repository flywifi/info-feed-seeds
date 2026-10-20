"""Offline injection pattern tier for fetched content.

Ported from the Creator OS `tools/injection_scan.py`, which implements the machine-scoreable half of
`shared/injection-guard-engine.md`: eight pattern categories with per-match points
(OVERRIDE 10, PERSONA 10, AUTHORITY 10, TEMPLATE 8, EXFIL 8, REDIRECT 5, ENCODE 5, SOCIAL 3), SOCIAL
zeroed when it is the only category present, and the bands CLEAN 0 to 2, REVIEW 3 to 7, QUARANTINE 8
to 15, BLOCK 16 and above. The verdict is carried in a field named `offline_pattern_scan`.

This port scores two views of the text and keeps the higher total: the raw text with HTML entities
unescaped, and a folded copy (NFKC normalised, zero-width and bidi characters stripped, common
confusable letters mapped to ASCII). `docs/injection-guard.md` records every place this code departs
from the engine document. `render_prior` emits only the level, score and category names, never raw
text, so the advisory line can never carry an injection itself.
"""
from __future__ import annotations

import re
import unicodedata
from html import unescape

FIELD_NAME = "offline_pattern_scan"

_SEVERITY = {
    "OVERRIDE": "CRITICAL", "PERSONA": "CRITICAL", "AUTHORITY": "CRITICAL",
    "TEMPLATE": "HIGH", "EXFIL": "HIGH",
    "REDIRECT": "MEDIUM", "ENCODE": "MEDIUM",
    "SOCIAL": "WARNING",
}
POINTS = {
    "OVERRIDE": 10, "PERSONA": 10, "AUTHORITY": 10,
    "TEMPLATE": 8, "EXFIL": 8,
    "REDIRECT": 5, "ENCODE": 5,
    "SOCIAL": 3,
}

_I = re.IGNORECASE


def _pats(*sources: str) -> list[re.Pattern]:
    return [re.compile(s, _I) for s in sources]


PATTERNS = {
    "OVERRIDE": _pats(
        r"\b(?:ignore|disregard|forget|override)\b[\w\s,]*?\b(?:instructions?|rules?|guidelines?|constraints?|previous|prior|all)\b",
        r"you no longer need to follow",
        r"your previous instructions are cancell?ed",
        r"starting now,? ignore everything above",
    ),
    "PERSONA": _pats(
        r"you are now (?:a|an|the)\b",
        r"\bact as (?:a|an|the)\b",
        r"pretend (?:you are|to be)\b",
        r"from now on you are\b",
        r"your new (?:role|instructions?|persona) is\b",
        r"\bDAN mode\b",
    ),
    "AUTHORITY": _pats(
        r"\bfrom (?:Anthropic|Claude|your creator|your developer|your trainer)\b",
        r"\bas (?:admin|administrator|system|operator|root)\b",
        r"I am your (?:developer|creator|trainer|supervisor)\b",
        r"system override",
        r"maintenance mode",
        r"developer access granted",
        r"debug mode activated",
    ),
    "TEMPLATE": _pats(
        r"<\s*(?:system|instructions?|prompt|rules)\s*>",
        r"\[/?(?:INST|SYS)\]",
        r"#{2,3}\s*(?:Instruction|System|Context)\b",
        r"---\s*BEGIN (?:SYSTEM|PROMPT)\s*---",
        r"---\s*END CONTEXT\s*---",
        r"\{(?:system_prompt|instructions)\}",
    ),
    "EXFIL": _pats(
        r"repeat (?:your|the) (?:system prompt|instructions|context)",
        r"what (?:are|were) your instructions",
        r"show me your (?:system|hidden|full) (?:prompt|instructions)",
        r"reveal your (?:training|guidelines|constraints)",
        r"print (?:everything above|your instructions)",
        r"output the (?:contents of|text of) your (?:context|system)",
    ),
    "REDIRECT": _pats(
        r"\b(?:fetch|call|request|load)\b[\w\s]*?https?://",
        r"send (?:this|the data|my information) to\b",
        r"(?:check|visit) (?:this|the following) (?:link|url|endpoint) for (?:your|new) instructions",
        r"for (?:updated|new) (?:instructions|rules)\b[\w\s]*?https?://",
    ),
    "ENCODE": _pats(
        r"base64 (?:encoded|message)",
        r"decode (?:this|the following) (?:message|string|instruction)",
        r"the following is (?:encoded|encrypted|hidden)",
        r"\bROT13\b",
        r"(?:hidden message|secret instruction)",
    ),
    "SOCIAL": _pats(
        r"act now before (?:your context|the session) (?:clears|resets|expires)",
        r"this message (?:will|must) be (?:processed|acted on) immediately",
        r"\b(?:URGENT|IMMEDIATELY|ACT NOW)\b",
    ),
}

DESCRIPTION = {
    "OVERRIDE": "instruction to disregard existing rules",
    "PERSONA": "attempt to redefine operating role",
    "AUTHORITY": "false claim of system-level authority",
    "TEMPLATE": "prompt-format / delimiter injection",
    "EXFIL": "attempt to extract the system prompt or config",
    "REDIRECT": "instruction to call an external URL not initiated by the user",
    "ENCODE": "obfuscated / encoded instruction",
    "SOCIAL": "urgency + pressure to bypass review",
}

CLEAN, REVIEW, QUARANTINE, BLOCK = "CLEAN", "REVIEW", "QUARANTINE", "BLOCK"
_RESPONSE = {CLEAN: "proceed", REVIEW: "surface_to_user", QUARANTINE: "quarantine", BLOCK: "block"}

# --------------------------------------------------------------------------- folding

_INVISIBLE = frozenset(
    "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u180e\u00ad"
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u061c"
)

# Letters from other scripts that render like ASCII letters. Cyrillic first, then Greek, then a
# few Latin letters with lookalike glyphs. Case is handled by mapping both cases where they exist.
CONFUSABLES = {
    # Cyrillic
    "\u0430": "a", "\u0410": "A", "\u0435": "e", "\u0415": "E", "\u043e": "o", "\u041e": "O",
    "\u0440": "p", "\u0420": "P", "\u0441": "c", "\u0421": "C", "\u0443": "y", "\u0423": "Y",
    "\u0445": "x", "\u0425": "X", "\u0456": "i", "\u0406": "I", "\u0458": "j", "\u0408": "J",
    "\u0455": "s", "\u0405": "S", "\u04bb": "h", "\u04ba": "H", "\u043a": "k", "\u041a": "K",
    "\u0432": "B", "\u0412": "B", "\u043d": "H", "\u041d": "H", "\u043c": "M", "\u041c": "M",
    "\u0442": "T", "\u0422": "T", "\u051b": "q", "\u051a": "Q", "\u0501": "d", "\u0500": "D",
    "\u051d": "w", "\u051c": "W", "\u04cf": "l", "\u04c0": "I", "\u0461": "w", "\u0460": "W",
    "\u0433": "r", "\u0493": "g", "\u0492": "G",
    # Greek
    "\u03b1": "a", "\u0391": "A", "\u03b2": "B", "\u0392": "B", "\u03b5": "e", "\u0395": "E",
    "\u03b9": "i", "\u0399": "I", "\u03ba": "k", "\u039a": "K", "\u03bd": "v", "\u039d": "N",
    "\u03bf": "o", "\u039f": "O", "\u03c1": "p", "\u03a1": "P", "\u03c4": "t", "\u03a4": "T",
    "\u03c5": "u", "\u03a5": "Y", "\u03c7": "x", "\u03a7": "X", "\u0396": "Z", "\u0397": "H",
    "\u039c": "M", "\u03b7": "n",
    # Latin lookalikes
    "\u0131": "i", "\u0261": "g", "\u0251": "a", "\u01c0": "l", "\u01c3": "!", "\u0237": "j",
    "\u1d00": "a", "\u1d04": "c", "\u1d07": "e", "\u1d0f": "o", "\u1d18": "p", "\u1d1b": "t",
}


def fold(text: str) -> str:
    """NFKC normalise, drop zero-width and bidi characters, map confusable letters to ASCII."""
    normalized = unicodedata.normalize("NFKC", text)
    out = []
    for ch in normalized:
        if ch in _INVISIBLE:
            continue
        out.append(CONFUSABLES.get(ch, ch))
    return "".join(out)


# --------------------------------------------------------------------------- scoring

def risk_level(score: int) -> str:
    if score >= 16:
        return BLOCK
    if score >= 8:
        return QUARANTINE
    if score >= 3:
        return REVIEW
    return CLEAN


def _score_view(text: str) -> tuple[int, list[dict]]:
    detected = []
    for cat, pats in PATTERNS.items():
        count = sum(len(p.findall(text)) for p in pats)
        if count:
            detected.append({"category": cat, "severity": _SEVERITY[cat],
                             "description": DESCRIPTION[cat], "match_count": count,
                             "points": POINTS[cat] * count})
    if {d["category"] for d in detected} == {"SOCIAL"}:
        detected = []
    return sum(d["points"] for d in detected), detected


def scan_text(text: str | None, trust: str = "untrusted_external",
              artifact_id: str | None = None) -> dict:
    """Score one content block and return the record carried as `offline_pattern_scan`.

    Two views are scored: `raw` (HTML entities unescaped) and `folded` (see `fold`). The higher
    total wins; `scored_view` says which. Ties go to `raw`."""
    raw = unescape(text or "")
    folded = fold(raw)
    raw_total, raw_detected = _score_view(raw)
    folded_total, folded_detected = _score_view(folded)
    if folded_total > raw_total:
        total, detected, view = folded_total, folded_detected, "folded"
    else:
        total, detected, view = raw_total, raw_detected, "raw"
    level = risk_level(total)
    rec = {
        "source_trust_class": trust,
        "total_score": total,
        "risk_level": level,
        "patterns_detected": detected,
        "quarantine_active": level in (QUARANTINE, BLOCK),
        "response": _RESPONSE[level],
        "surfaced_to_user": level != CLEAN,
        "scored_view": view,
        "views": {"raw": raw_total, "folded": folded_total},
    }
    if artifact_id:
        rec["source_artifact_id"] = artifact_id
    if level != CLEAN:
        cats = ", ".join(sorted(d["category"] for d in detected))
        rec["user_message"] = (
            f"This content matched prompt-injection patterns (offline pattern score {total}, "
            f"{level}). Detected: {cats}. This is the offline pattern tier; a reworded attack can "
            f"pass it. Review the exact matches before using this content.")
    return rec


def scan_item_fields(*fields: str | None, trust: str = "untrusted_external") -> dict:
    """Scan the concatenation of an item's text fields (title, summary, authors) as one block."""
    return scan_text("\n".join(f for f in fields if f), trust=trust)


def render_prior(rec: dict | None) -> str:
    """One advisory line: level, score and category names with counts. Never raw text."""
    if not isinstance(rec, dict) or "risk_level" not in rec:
        return "no offline pre-filter result (this content was not screened offline)"
    level = rec.get("risk_level", CLEAN)
    score = rec.get("total_score", 0)
    cats = [d for d in (rec.get("patterns_detected") or [])
            if isinstance(d, dict) and d.get("category")]
    if not cats:
        return f"{level} (offline pattern score {score}); no known injection patterns matched"
    named = ", ".join(f"{d['category']} x{d.get('match_count', 1)}" for d in cats)
    return f"{level} (offline pattern score {score}): {named}"


__all__ = [
    "BLOCK", "CLEAN", "CONFUSABLES", "DESCRIPTION", "FIELD_NAME", "PATTERNS", "POINTS",
    "QUARANTINE", "REVIEW", "fold", "render_prior", "risk_level", "scan_item_fields", "scan_text",
]
