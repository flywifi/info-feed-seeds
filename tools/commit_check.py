#!/usr/bin/env python3
"""Commit hygiene for info-feed-seeds: the checks every commit, push and pull request must pass.

Usage:
  python3 tools/commit_check.py --staged                 pre-commit: staged files and the author
  python3 tools/commit_check.py --message-file FILE      commit-msg: one commit message
  python3 tools/commit_check.py --pre-push REMOTE URL    pre-push: reads git's ref lines on stdin
  python3 tools/commit_check.py --range A..B | auto      CI: every commit and changed file in a range
  python3 tools/commit_check.py --sources A..B | auto    CI: proof records of changed source folders
  python3 tools/commit_check.py --main-history A..B      CI: commits that landed on main (stricter web rule)
  python3 tools/commit_check.py --pr N                   a pull request's title and body (gh api, REST)
  python3 tools/commit_check.py --pr-event FILE          the same, from a GitHub Actions event payload
  python3 tools/commit_check.py --selftest               prove the checks fire, in a scratch repository

Checks
  messages   no co-author or session trailer, no session link or id, no footer (see the footer
             rules below), no em or en dash. Text is normalised first (NFKD, format characters and
             marks removed, Unicode hyphens and lookalike letters folded, HTML entities and
             percent-encoding decoded).
  files      the same rules read as CLAUDE.md words them: a trailer LINE, a LINK, a footer LINE, so
             a sentence that names the rule (as CLAUDE.md does) passes. Also UTF-8, no CR, no em or
             en dash, ASCII-only .py; never local/, .cache/, logs/, .venv-crawl/, *.local.json or
             .secrets.local.json; never digests/, state/ or ledger/ off the data branch.
  identity   author and committer are flywifi <236871844+flywifi@users.noreply.github.com>. GitHub's
             web-flow committer is accepted only on a commit already on origin/main (it landed
             through GitHub); anywhere else it is treated as forged.
  pushes     the push URL is exactly github.com/flywifi/info-feed-seeds; never to main or data.
  ranges     an empty range is a failure (fail closed), except a branch deletion in pre-push.

`--range auto` uses origin/$BASE_REF (default main)..HEAD, falls back to the policy boundary commit
(BOUNDARY_SHA), and fails closed when neither resolves. The checker works from any copy of this file
(CI runs the base branch's copy) and always inspects the repository in the current directory.
Output is ASCII only. Exit 0 clean, 1 on any finding or error, 2 on a usage error. Standard library.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import unquote

REPO_SLUG = "flywifi/info-feed-seeds"
BOUNDARY_SHA = "5436f99"
AUTHOR = ("flywifi", "236871844+flywifi@users.noreply.github.com")
WEB_COMMITTER = ("GitHub", "noreply@github.com")
PROTECTED_BRANCHES = ("main", "data")
ZERO_SHA = "0" * 40
SCISSORS = "# ------------------------ >8 ------------------------"
SELF_PATHS = ("tools/commit_check.py", "tools/hooks/", ".github/workflows/", "tools/main_history_known.txt",
              "tests/fixtures/refused_corpus.jsonl", "tests/fixtures/allowed_corpus.jsonl",
              "tests/fixtures/pending_ruling.jsonl", "tests/fixtures/corpus.lock",
              "tests/fixtures/checker_baseline.py", "tests/fixtures/renamed_ids.txt", "tests/test_corpus.py",
              "tests/test_no_narrowing.py", "tools/corpus_added.py")

EM_DASH, EN_DASH = "\u2014", "\u2013"
# Every dash-like character a renderer could show as an em or en dash. Checked after HTML entities
# are decoded, because GitHub renders the named HTML entities for both dashes as the characters.
DASHES = (
    ("\u2014", "em dash"), ("\u2013", "en dash"), ("\u2015", "horizontal bar"),
    ("\u2e3a", "two-em dash"), ("\u2e3b", "three-em dash"), ("\ufe58", "small em dash"),
    ("\ufe31", "vertical em dash"),
)

# The banned strings are assembled from pieces so this file never contains what it hunts for.
#
# Every rule below is checked against tests/fixtures/refused_corpus.jsonl (all must be refused) and
# tests/fixtures/allowed_corpus.jsonl (all must pass), and tests/test_no_narrowing.py fails when the
# 512e818 checker (tests/fixtures/checker_baseline.py) refuses a variant this one lets through.
# Separator runs are unbounded, as in the baseline, but every unbounded run either follows a literal
# or is anchored at a line start and cannot cross a newline, and no two unbounded runs can trade
# characters, so matching stays linear; a timing test runs each rule on 100,000-character lines.
_SEP = r"[\W_]*"
_CO = "co" + _SEP + "author(?:ed)?" + _SEP + "by"
_SESSION = "claude" + _SEP + "session"
_HOST = "claude" + r"\s*\.\s*" + "ai"
# A line may open with symbols, digits, underscores, quote markers and list markers, then a label:
# a letter, up to 60 letters, digits, spaces or apostrophes ("Note", "Tool's note", "Automatically
# produced release notes"), any symbols, a colon and any symbols.
_LABEL = r"(?:[a-z][a-z0-9 ']{0,60}[^a-z0-9:\n]*:(?:[^\w\n]|_)*)?"
_LINE_START = r"^(?:[^\w\n]|[_\d])*" + _LABEL
_PHRASE = "generated" + r"[\W_]+(?:(?:with|by|using|via)(?![a-z0-9])|w/)"
_NAME = "claude"
# The name may run straight into "ai" ("...AI") or digits; any other letter ends the match.
_NAME_END = r"(?:ai)?(?![a-z])"
# Footer rules:
#   line     a line opening with "Generated" with, by, using or via in any case, after an optional
#            label, whatever tool it names (finding 7, read literally and failing closed until it
#            is ruled on)
#   product  anywhere, the phrase followed by separators only and then the assistant's name
#   sentence anywhere, the phrase and then the assistant's name later in the same sentence, within
#            200 characters, across single line breaks and dots inside words or numbers ("4.5"):
#            any statement that something was generated with, by, using or via it
#   near     anywhere, the phrase and the assistant's name later in the same paragraph, within
#            1,000 characters (a bound for speed only)
#   tool     a line opening with the phrase that names the assistant within a few words
#   code     anywhere, the phrase and then the assistant's name and "code" within a few words
#   paren    anywhere, the phrase and then the assistant's name in parentheses within a few words
_GEN_LINE = _LINE_START + _PHRASE
_GEN_PRODUCT = _PHRASE + _SEP + r"\[?" + _NAME + _NAME_END


class _PhraseThenName:
    """A start phrase (by default the footer phrase), then the assistant's name starting within
    `reach` characters, in a window that ends at the first `stop` match. Equivalent to the phrase,
    a lazy gap of up to `reach` characters that never contains `stop`, and the name, but linear:
    each phrase is followed by one window, searched twice in C."""

    def __init__(self, reach: int, stop: str, start: str = _PHRASE):
        self.reach = reach
        self.start = re.compile(start)
        self.stop = re.compile(stop)
        self.name = re.compile(_NAME + _NAME_END)
        self.pattern = f"{start} ...{reach} (stop {stop}) {_NAME}"

    def search(self, text: str):
        for m in self.start.finditer(text):
            end = m.end()
            window = text[end:end + self.reach + len(_NAME) + 2]
            if _NAME not in window:
                continue
            cut = self.stop.search(window)
            if cut:
                window = window[:cut.start()]
            # One character of context before the window keeps offsets comparable with `reach`.
            hit = self.name.search(text[end - 1:end] + window)
            if hit and hit.start() - 1 <= self.reach:
                return hit
        return None


# sentence: stop at a blank line, a line break followed by another, "!" or "?", or a dot that is not
# inside a word or number. near: stop only at a blank line.
_SENTENCE_STOP = r"\n\n|[!?]|\.(?![a-z0-9])"
_GEN_SENTENCE = _PhraseThenName(200, _SENTENCE_STOP)
_GEN_NEAR = _PhraseThenName(1000, r"\n\n")
# Authorship statements (Matt's ruling, 2026-10-23): text says what changed, why and how it works,
# never which tool wrote it. A feature that uses the assistant may name it ("the classifier calls
# the API"); a claim that it made the work may not.
#   made-by  a making verb, also inside a longer word ("rebuilt", "ghostwritten") and "assisted"
#            (so an "Assisted-by:" trailer is refused), up to 3 words, then with, by, using, via,
#            w/, through, alongside or together with, then the name later in the same sentence,
#            within 200 characters
#   credit   thanks to, courtesy of, with the help of, with help from, powered by or in
#            collaboration with, directly before the name
#   by       "by" directly before the name ("Code by ..."), failing closed until Matt rules
#   label    an "author:" label before the name
#   first    I or we, up to 2 words, then used, use, asked, had or let, up to 2 words, then the
#            name; or "using" the name, up to 2 words, then I or we
#   author   the name, up to 2 words (a product name such as "Code" or "Opus"), then wrote, built,
#            made, created, authored, drafted, coded, produced, generated or helped
# No rule has a left-boundary lookbehind (audit 8): a name or verb inside a longer word counts.
# A word in a gap is 1 to 30 letters, digits or apostrophes, so every gap is bounded.
_WHO = _NAME + _NAME_END
_W2 = r"(?:[\W_]+[a-z0-9']{1,30}){0,2}?"
_W3 = r"(?:[\W_]+[a-z0-9']{1,30}){0,3}?"
_MADE = (r"(?:built|made|created|written|wrote|authored|produced|coded|developed|drafted|"
         r"composed|crafted|prepared|designed|assisted)" + _W3 + r"[\W_]+(?:(?:with|by|using|via|through|"
         r"alongside|together[\W_]+with)(?![a-z0-9])|w/)")
_AUTHOR_MADE = _PhraseThenName(200, _SENTENCE_STOP, start=_MADE)
_AUTHOR_CREDIT = (r"(?:thanks[\W_]+to|courtesy[\W_]+of|with[\W_]+the[\W_]+help[\W_]+of|"
                  r"with[\W_]+help[\W_]+from|powered[\W_]+by|in[\W_]+collaboration[\W_]+with)[\W_]+" + _WHO)
_AUTHOR_BY = r"by[\W_]+" + _WHO
_AUTHOR_LABEL = r"author[\W_]*[:=][\W_]*" + _WHO
_AUTHOR_FIRST = (r"(?:i|we)" + _W2 + r"[\W_]+(?:used|use|asked|had|let)" + _W2 + r"[\W_]+" + _WHO
                 + r"|using[\W_]+" + _WHO + _W2 + r"[\W_]+(?:i|we)(?![a-z0-9])")
_AUTHOR_NAME = (_WHO + _W2 + r"[\W_]+(?:wrote|built|made|created|authored|drafted|coded|produced|generated|"
                r"helped)(?![a-z0-9])")
_GEN_TOOL = _LINE_START + _PHRASE + _SEP + ".{0,24}?" + _NAME
_GEN_CODE = _PHRASE + _SEP + ".{0,24}?" + _NAME + _SEP + "code(?![a-z0-9])"
_GEN_PAREN = _PHRASE + _SEP + r".{0,24}?\(\s*" + _NAME + r"\s*\)"
# Footer rules also run on a copy with HTML tags removed, so <p> or <sub> cannot hide a line start.
_TAG = re.compile(r"<[^<>\n]*>")
FOOTER_RULES = (
    ("generated footer", re.compile(_GEN_LINE, re.M)),
    ("generated footer", re.compile(_GEN_PRODUCT)),
    ("generated footer", _GEN_SENTENCE),
    ("generated footer", _GEN_NEAR),
    ("generated footer", re.compile(_GEN_TOOL, re.M)),
    ("generated footer", re.compile(_GEN_CODE)),
    ("generated footer", re.compile(_GEN_PAREN)),
    ("authorship statement", _AUTHOR_MADE),
    ("authorship statement", re.compile(_AUTHOR_CREDIT)),
    ("authorship statement", re.compile(_AUTHOR_BY)),
    ("authorship statement", re.compile(_AUTHOR_LABEL)),
    ("authorship statement", re.compile(_AUTHOR_FIRST)),
    ("authorship statement", re.compile(_AUTHOR_NAME)),
)
# Rules that need the original letter case (run on a normalised copy that keeps case).
CASED_FOOTER_RULES = ()  # none at present; the near rule no longer depends on letter case

# A trailer line: a line that opens (after symbols, numbers, list markers or a short label) with
# the key and then a colon or an equals sign.
_CO_LINE = _LINE_START + _CO + _SEP + "[:=]"
_SESSION_LINE = _LINE_START + _SESSION + _SEP + "[:=]"
# Anywhere, the co-author key followed within 60 characters by an address in angle brackets.
_CO_ADDRESS = _CO + r".{0,60}?<[^<>\s@]{1,64}@[^<>\s]{1,255}>"
# A bare session id: "session_" and at least 20 letters or digits.
_SESSION_ID = r"session_(?:[-_]?[a-z0-9]){20}"
MESSAGE_RULES = (
    # A co-author trailer: a trailer line; or anywhere, the key with a hyphen before "by" (which
    # prose such as "co-authored by Smith" never has); or the key and an address.
    ("co-author trailer", re.compile(_CO_LINE, re.M)),
    ("co-author trailer", re.compile("co" + _SEP + "author(?:ed)?" + "-" + "by")),
    ("co-author trailer", re.compile(_CO_ADDRESS)),
    ("session trailer", re.compile(_SESSION)),
    ("session link", re.compile(_HOST + r"\b")),
    ("session id", re.compile(_SESSION_ID)),
) + FOOTER_RULES
# Files name the banned items in prose (CLAUDE.md does), so only their real forms are refused.
FILE_RULES = (
    ("co-author trailer line", re.compile(_CO_LINE, re.M)),
    ("co-author trailer", re.compile(_CO_ADDRESS)),
    ("session trailer line", re.compile(_SESSION_LINE, re.M)),
    ("session id", re.compile(_SESSION_ID)),
    ("session link", re.compile(r"(?:[a-z0-9+.-]:/*|//|www\.|[a-z0-9-]\.)" + _HOST + r"\b|"
                                + _HOST + r"(?=[/:?#])")),
) + FOOTER_RULES

_HYPHENS = {ord(c): "-" for c in "\u00ad\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"}
# Letters from other scripts that render like the ASCII letters in the banned words.
# Capital letters from other scripts that look like Latin capitals; folded before lowercasing so a
# capital lookalike cannot slip past the small-letter table below.
_CAPITAL_CONFUSABLES = {ord(k): v for k, v in {
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0423": "Y", "\u0425": "X",
    "\u0405": "S", "\u0406": "I", "\u0408": "J", "\u04c0": "I", "\u051a": "Q", "\u051c": "W",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
    "\u03a5": "Y", "\u03a7": "X",
    # Cherokee capitals (their lowercase forms are folded below) and Lisu letters.
    "\u13aa": "A", "\u13f4": "B", "\u13df": "C", "\u13a0": "D", "\u13ac": "E", "\u13c0": "G",
    "\u13bb": "H", "\u13a5": "i", "\u13ab": "J", "\u13e6": "K", "\u13de": "L", "\u13b7": "M",
    "\u13e2": "P", "\u13a1": "R", "\u13da": "S", "\u13a2": "T", "\u13b3": "W", "\u13a9": "Y",
    "\u13c3": "Z", "\u13cf": "b", "\u13c2": "h", "\u13d9": "V",
    "\ua4ee": "A", "\ua4d0": "B", "\ua4da": "C", "\ua4d3": "D", "\ua4f0": "E", "\ua4d6": "G",
    "\ua4e7": "H", "\ua4f2": "I", "\ua4d9": "J", "\ua4d7": "K", "\ua4e1": "L", "\ua4df": "M",
    "\ua4e0": "N", "\ua4f3": "O", "\ua4d1": "P", "\ua4e3": "R", "\ua4e2": "S", "\ua4d4": "T",
    "\ua4f4": "U", "\ua4e6": "V", "\ua4ea": "W", "\ua4ec": "Y", "\ua4dc": "Z",
}.items()}
# Characters that render as blank but are not format (Cf) characters: Hangul fillers, the blank
# braille pattern and the null notehead.
_FILLERS = frozenset("\u3164\u115f\u1160\uffa0\u2800\U0001d159")
# Dots that IDNA treats as the label separator, so "claude" + ideographic dot + "ai" opens the host.
_DOTS = {ord(c): "." for c in "\u3002\uff61\uff0e"}
_CONFUSABLES = {ord(k): v for k, v in {
    "\u0430": "a", "\u0441": "c", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0443": "y",
    "\u0445": "x", "\u0456": "i", "\u0455": "s", "\u0501": "d", "\u04bb": "h", "\u051d": "w",
    "\u04cf": "l", "\u0261": "g", "\u0131": "i", "\u03b1": "a", "\u03bf": "o", "\u03b5": "e",
    "\u03b9": "i", "\u03bd": "v", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u0251": "a",
    # Latin small capitals, Armenian, and a few more Greek, Cyrillic and Latin lookalikes.
    "\u1d00": "a", "\u0299": "b", "\u1d04": "c", "\u1d05": "d", "\u1d07": "e", "\u0262": "g",
    "\u029c": "h", "\u026a": "i", "\u1d0a": "j", "\u1d0b": "k", "\u029f": "l", "\u1d0d": "m",
    "\u0274": "n", "\u1d0f": "o", "\u1d18": "p", "\u0280": "r", "\ua731": "s", "\u1d1b": "t",
    "\u1d1c": "u", "\u1d20": "v", "\u1d21": "w", "\u028f": "y", "\u1d22": "z",
    "\u0585": "o", "\u0578": "n", "\u057d": "u", "\u0570": "h", "\u0581": "g", "\u0566": "q",
    "\u03ba": "k", "\u051b": "q", "\u0458": "j", "\u0269": "i", "\u0237": "j",
}.items()}
# Cherokee small letters, which lowercasing makes of the capitals above.
_CONFUSABLES.update({ord(chr(k).lower()): v.lower() for k, v in _CAPITAL_CONFUSABLES.items()
                     if 0x13a0 <= k <= 0x13f5})

FORBIDDEN_PREFIXES = ("local/", ".cache/", "logs/", ".venv-crawl/")
FORBIDDEN_NAMES = re.compile(r"(?:^|/)(?:[^/]*\.local\.json|\.secrets\.local\.json)$")
DATA_PREFIXES = ("digests/", "state/", "ledger/")
_GITHUB_URL = (re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?github\.com(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$", re.I),
               re.compile(r"^(?:[^@/]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", re.I))


def say(message: str = "") -> None:
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def git(*args: str, cwd: Path | None = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def git_ok(*args: str, cwd: Path | None = None) -> bool:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True).returncode == 0


def toplevel(cwd: Path | None = None) -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=cwd).decode("utf-8").strip())


# --------------------------------------------------------------------------- text checks

def _decode_layers(text: str) -> str:
    """HTML entities decoded once, as a browser renders them (an escaped entity such as the one in
    the allowed entry audit3_fp_amp_ndash_literal_word stays literal text), then percent-encoding
    decoded until nothing changes (at most 8 rounds), so a text encoded several times over
    ("%2525E2...") is read as it finally reads (audit 8)."""
    value = html.unescape(text or "")
    for _ in range(8):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def normalize(text: str, keep_case: bool = False) -> str:
    """Fold the tricks that hide a banned string: entities, percent-encoding, compatibility forms
    (fullwidth letters and dots), format characters (zero-width, bidi), combining and enclosing
    marks, Hangul fillers, the blank braille pattern, ideographic dots, Unicode hyphens and
    lookalike letters in either case. Lowercased."""
    value = _decode_layers(text)
    # NFKD, not NFKC: decomposing first means an accented letter (precomposed or built from a
    # combining mark) always leaves a combining mark (Mn) behind, which the next line removes.
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value
                    if unicodedata.category(ch) not in ("Cf", "Mn", "Me") and ch not in _FILLERS)
    value = value.translate(_DOTS).translate(_HYPHENS).translate(_CAPITAL_CONFUSABLES)
    return value.translate(_CONFUSABLES) if keep_case else value.lower().translate(_CONFUSABLES)


def _decode_dashes(text: str) -> str:
    """Entities and percent-encoding decoded to a fixed point, as a renderer or a browser would
    show them."""
    return _decode_layers(text)


def _dash_findings(text: str, where: str) -> list[str]:
    decoded = _decode_dashes(text)
    return [f"{where}: contains an {name}" if name[0] in "ae" else f"{where}: contains a {name}"
            for ch, name in DASHES if ch in decoded]


def _rule_findings(text: str, where: str, rules) -> list[str]:
    folded = normalize(text)
    views = {"plain": (folded, _TAG.sub("", folded))}
    if CASED_FOOTER_RULES:
        cased = normalize(text, keep_case=True)
        views["cased"] = (cased, _TAG.sub("", cased))
    footer = {id(r) for _, r in FOOTER_RULES}
    labels = []
    checks = [(label, pattern, "plain") for label, pattern in rules]
    if any(id(r) in footer for _, r in rules):
        checks += [(label, pattern, "cased") for label, pattern in CASED_FOOTER_RULES]
    for label, pattern, view in checks:
        if label in labels:
            continue  # one finding per label is enough
        text_view, untagged = views[view]
        is_footer = view == "cased" or id(pattern) in footer
        hit = pattern.search(text_view) or (is_footer and untagged != text_view and pattern.search(untagged))
        if hit:
            labels.append(label)
    return [f"{where}: contains a {label}" for label in labels]


def text_findings(text: str, where: str) -> list[str]:
    """Commit messages, pull request titles and bodies: any mention is refused."""
    return _rule_findings(text, where, MESSAGE_RULES) + _dash_findings(text, where)


def identity_findings(author, committer, where: str, on_main: bool = False) -> list[str]:
    out = []
    if tuple(author) != AUTHOR:
        out.append(f"{where}: author is {author[0]} <{author[1]}>, expected {AUTHOR[0]} <{AUTHOR[1]}>")
    if committer is not None and tuple(committer) != AUTHOR:
        if tuple(committer) == WEB_COMMITTER and on_main:
            pass  # landed on main through GitHub (web merge or web edit)
        elif tuple(committer) == WEB_COMMITTER:
            out.append(f"{where}: committer claims to be GitHub but is not trusted here: not on main "
                       "(job ranges), or not GitHub-verified (main history)")
        else:
            out.append(f"{where}: committer is {committer[0]} <{committer[1]}>, expected {AUTHOR[0]} <{AUTHOR[1]}>")
    return out


def path_findings(path: str, branch: str | None) -> list[str]:
    out = []
    if path.startswith(FORBIDDEN_PREFIXES) or FORBIDDEN_NAMES.search(path):
        out.append(f"{path}: this path is never tracked")
    if path.startswith(DATA_PREFIXES) and branch != "data":
        out.append(f"{path}: machine output belongs only on the data branch")
    return out


# One file describes the rules in words the rules refuse: the 512e818 checker, kept byte for byte
# as the no-narrowing baseline. Only that exact path with exactly these bytes skips the text rules;
# any change to it makes it an ordinary file again (audit 7, M3). CLAUDE.md was pinned too until
# its rule paragraph was split (Matt's ruling, 2026-10-23); it is now checked like any file.
BASELINE_PATH = "tests/fixtures/checker_baseline.py"
BASELINE_SHA256 = "fd224233c718939f7f4ce29cd1b758efeb49dc1550901e3339f42bdd782ad05a"
PINNED_FILES = {
    BASELINE_PATH: BASELINE_SHA256,
}


def content_findings(path: str, data: bytes) -> list[str]:
    if b"\x00" in data[:8192]:
        # Binary by git's test. A NUL byte must not hide text, so the text rules still run on a
        # decoded copy (audit 8); the line-ending, dash and ASCII checks apply to text files only.
        pinned = path in PINNED_FILES and hashlib.sha256(data).hexdigest() == PINNED_FILES[path]
        return [] if pinned else _rule_findings(data.decode("utf-8", "replace").replace("\x00", ""), path, FILE_RULES)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: not UTF-8 (byte {exc.start})"]
    pinned = path in PINNED_FILES and hashlib.sha256(data).hexdigest() == PINNED_FILES[path]
    out = [] if pinned else _rule_findings(text, path, FILE_RULES)
    if "\r" in text:
        out.append(f"{path}: has CR line endings")
    decoded = _decode_dashes(text)
    for ch, name in DASHES:
        if ch in decoded:
            line = decoded[: decoded.index(ch)].count("\n") + 1
            out.append(f"{path}:{line}: contains {'an' if name[0] in 'ae' else 'a'} {name}")
    if path.endswith(".py"):
        for index, ch in enumerate(text):
            if ord(ch) > 127:
                line = text[:index].count("\n") + 1
                out.append(f"{path}:{line}: non-ASCII character in a Python file (use \\u escapes)")
                break
    return out


# --------------------------------------------------------------------------- ranges

def base_ref() -> str:
    return f"origin/{os.environ.get('BASE_REF') or 'main'}"


def resolve_range(spec: str, cwd: Path | None = None) -> str | None:
    """'auto' -> origin/<BASE_REF>..HEAD, else BOUNDARY..HEAD, else None (fail closed)."""
    if spec != "auto":
        return spec
    if git_ok("rev-parse", "--verify", "--quiet", base_ref() + "^{commit}", cwd=cwd):
        return f"{base_ref()}..HEAD"
    if git_ok("rev-parse", "--verify", "--quiet", BOUNDARY_SHA + "^{commit}", cwd=cwd):
        return f"{BOUNDARY_SHA}..HEAD"
    return None


def github_verified(sha: str) -> bool:
    """True when GitHub's API marks the commit as verified (signed by GitHub for web commits).
    Any failure to ask counts as not verified, so the check fails closed."""
    proc = subprocess.run(["gh", "api", f"repos/{REPO_SLUG}/commits/{sha}", "--jq", ".commit.verification.verified"],
                          capture_output=True)
    return proc.returncode == 0 and proc.stdout.decode().strip() == "true"


def commits_in(rng: str, cwd: Path | None = None, main_history: bool = False, verify=github_verified) -> list[dict]:
    """Every commit in the range. On a job-branch range a web-flow committer is trusted when the
    commit is already on origin/main, because main-history checked it when it landed. In
    main-history mode it is trusted only on a commit GitHub marks verified, merges included: a
    forged merge has the right shape but no GitHub signature."""
    shas = git("rev-list", "--reverse", rng, cwd=cwd).decode().split()
    main_ok = git_ok("rev-parse", "--verify", "--quiet", "origin/main^{commit}", cwd=cwd)
    out = []
    for sha in shas:
        raw = git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha, cwd=cwd)
        an, ae, cn, ce, body = raw.decode("utf-8", "replace").split("\x00", 4)
        if main_history:
            web_ok = (cn, ce) == WEB_COMMITTER and verify(sha)
        else:
            web_ok = main_ok and git_ok("merge-base", "--is-ancestor", sha, "origin/main", cwd=cwd)
        out.append({"sha": sha, "author": (an, ae), "committer": (cn, ce), "message": body, "on_main": web_ok})
    return out


def changed_paths(rng: str, cwd: Path | None = None) -> tuple[str, list[str]]:
    """(tip, paths added or modified between the range's merge base and its tip)."""
    start, _, tip = rng.partition("..")
    tip = tip or "HEAD"
    raw = git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", f"{start}...{tip}", cwd=cwd)
    return tip, [p for p in raw.decode("utf-8", "replace").split("\x00") if p]


KNOWN_FILE = "tools/main_history_known.txt"


def load_known(tip: str, in_range: set[str], cwd: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Acknowledged commits on main: one 'full-sha reason' per line, '#' comments, read from the tip
    of the range being checked. Main cannot be rewritten, so a commit that landed with a finding
    would otherwise keep every later main-history run red and hide new problems.

    An entry is honoured only if the listed commit was already on main before the change that added
    the entry: the first-parent commit on main that introduced the line must have the listed commit
    in its first parent. So a pull request cannot carry a bad commit and acknowledge it in advance.
    Entries for commits outside the range are flagged."""
    known: dict[str, str] = {}
    problems: list[str] = []
    try:
        text = git("show", f"{tip}:{KNOWN_FILE}", cwd=cwd).decode("utf-8")
    except RuntimeError:
        return known, problems
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sha, _, reason = line.partition(" ")
        where = f"{KNOWN_FILE}:{number}"
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or not reason.strip():
            problems.append(f"{where}: expected a full 40-character sha and a reason")
            continue
        if sha not in in_range:
            problems.append(f"{where}: {sha[:7]} is not a commit in the range checked; remove the entry")
            continue
        # --first-parent also diffs each merge against its first parent, so the merge of the pull
        # request that added the line is the commit found.
        adders = git("log", "--first-parent", "--format=%H", f"-S{sha}", tip, "--", KNOWN_FILE,
                     cwd=cwd).decode().split()
        adder = adders[-1] if adders else None
        if adder is None or not git_ok("merge-base", "--is-ancestor", sha, f"{adder}^1", cwd=cwd):
            problems.append(f"{where}: {sha[:7]} was acknowledged by the same change that brought it to main "
                            "(or before it landed); an acknowledgement must follow the landing")
            continue
        known[sha] = reason.strip()
    return known, problems


def check_range(rng: str, branch: str | None = None, cwd: Path | None = None, main_history: bool = False,
                verify=github_verified) -> list[str]:
    commits = commits_in(rng, cwd=cwd, main_history=main_history, verify=verify)
    if not commits:
        say(f"commit_check: 0 commit(s) in {rng}")
        return [f"{rng}: the range holds no commits; failing closed"]
    findings: list[str] = []
    tip = rng.partition("..")[2] or "HEAD"
    known, known_problems = (load_known(tip, {c["sha"] for c in commits}, cwd) if main_history else ({}, []))
    findings += known_problems
    for c in commits:
        where = f"commit {c['sha'][:7]}"
        mine = text_findings(c["message"], where + " message")
        mine += identity_findings(c["author"], c["committer"], where, on_main=c["on_main"])
        if mine and c["sha"] in known:
            for f in mine:
                say(f"commit_check: known, acknowledged in {KNOWN_FILE} ({known[c['sha']]}): {f}")
            continue
        findings += mine
    tip, paths = changed_paths(rng, cwd=cwd)
    for path in paths:
        findings += path_findings(path, branch)
        findings += content_findings(path, git("show", f"{tip}:{path}", cwd=cwd))
    touched = sorted(p for p in paths if p.startswith(SELF_PATHS))
    if touched:
        say("commit_check: NOTICE: this range changes the checks themselves (" + ", ".join(touched)
            + "); CI runs the base branch's checker, and a person must review these files")
    say(f"commit_check: {len(commits)} commit(s), {len(paths)} changed file(s) in {rng}")
    return findings


# --------------------------------------------------------------------------- modes

def current_branch(cwd: Path | None = None) -> str | None:
    try:
        name = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).decode().strip()
    except RuntimeError:
        return None
    return None if name == "HEAD" else name


def check_staged(cwd: Path | None = None) -> list[str]:
    findings: list[str] = []
    for var, role in (("GIT_AUTHOR_IDENT", "author"), ("GIT_COMMITTER_IDENT", "committer")):
        ident = git("var", var, cwd=cwd).decode("utf-8", "replace")
        m = re.match(r"^(.*) <([^>]*)>", ident)
        if not m:
            findings.append(f"cannot read the {role} identity for this commit")
        elif (m.group(1), m.group(2)) != AUTHOR:
            findings.append(f"{role} for this commit is {m.group(1)} <{m.group(2)}>, "
                            f"expected {AUTHOR[0]} <{AUTHOR[1]}>")
    raw = git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=d", cwd=cwd)
    paths = [p for p in raw.decode("utf-8", "replace").split("\x00") if p]
    branch = current_branch(cwd)
    for path in paths:
        findings += path_findings(path, branch)
        findings += content_findings(path, git("show", f":{path}", cwd=cwd))
    say(f"commit_check: {len(paths)} staged file(s)")
    return findings


def check_message_text(text: str) -> list[str]:
    """Everything above git's scissors line is checked, comment lines included: whether git strips
    them depends on the cleanup mode, so a banned line hidden behind '#' is still refused."""
    kept = text.split(SCISSORS, 1)[0]
    return text_findings(kept, "commit message")


def remote_names_repo(url: str) -> bool:
    for pattern in _GITHUB_URL:
        m = pattern.match((url or "").strip())
        if m:
            return f"{m.group(1)}/{m.group(2)}".lower() == REPO_SLUG
    return False


def check_pre_push(remote: str, url: str, lines: list[str], cwd: Path | None = None) -> list[str]:
    findings: list[str] = []
    if not remote_names_repo(url):
        findings.append(f"push URL {url!r} is not github.com/{REPO_SLUG}")
    try:
        push_url = git("remote", "get-url", "--push", "origin", cwd=cwd).decode().strip()
        if not remote_names_repo(push_url):
            findings.append(f"origin push URL {push_url!r} is not github.com/{REPO_SLUG}")
    except RuntimeError as exc:
        findings.append(str(exc))
    valid = 0
    for line in lines:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4 or not all(re.fullmatch(r"[0-9a-f]{40}", x) for x in (parts[1], parts[3])):
            findings.append(f"malformed pre-push line {line[:80]!r}; failing closed")
            continue
        valid += 1
        local_ref, local_sha, remote_ref, remote_sha = parts
        target = remote_ref.removeprefix("refs/heads/")
        if target in PROTECTED_BRANCHES:
            findings.append(f"refusing to push to {target}: changes reach {target} only through pull "
                            "requests (main) or the poll workflow (data)")
            continue
        if local_sha == ZERO_SHA:
            continue  # deleting a job branch: the one empty push allowed
        if remote_sha != ZERO_SHA and git_ok("cat-file", "-e", remote_sha + "^{commit}", cwd=cwd):
            rng = f"{remote_sha}..{local_sha}"
        else:
            base = resolve_range("auto", cwd=cwd)
            if base is None:
                findings.append("no base to compare against (origin/main and the boundary are both missing)")
                continue
            rng = base.replace("HEAD", local_sha)
        findings += check_range(rng, branch=target, cwd=cwd)
    if valid == 0:
        findings.append("git passed no refs to push; failing closed")
    return findings


def check_sources(rng: str, cwd: Path | None = None) -> list[str]:
    """Every added or changed sources/<id>/ folder carries a live proof with a sample item.
    Stands in for `hub check --prove` until that command exists."""
    if not git("rev-list", rng, cwd=cwd).split():
        return [f"{rng}: the range holds no commits; failing closed"]
    root = toplevel(cwd)
    _, paths = changed_paths(rng, cwd=cwd)
    ids = sorted({p.split("/")[1] for p in paths
                  if p.startswith("sources/") and p.count("/") >= 2 and p.split("/")[1] != "retired"})
    findings = []
    for sid in ids:
        folder = root / "sources" / sid
        if not folder.is_dir():
            continue
        try:
            manifest = json.loads((folder / "source.json").read_text(encoding="utf-8"))
            methods = json.loads((folder / "methods.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            findings.append(f"sources/{sid}: cannot read source.json and methods.json ({exc})")
            continue
        feed_urls = {r.get("url") for r in manifest.get("routes", []) if r.get("adapter") == "feed"}
        proven = False
        for rec in methods.get("routes", []):
            if rec.get("url") not in feed_urls or not rec.get("attempts"):
                continue
            last = rec["attempts"][-1]
            if (last.get("source_label") == "live" and last.get("http_status") == 200
                    and last.get("result") == "ok" and last.get("tried_at") and last.get("sample")):
                proven = True
        if not proven:
            findings.append(f"sources/{sid}: no feed route whose latest attempt is a live 200 with a sample item")
        if not manifest.get("verified"):
            findings.append(f"sources/{sid}: verified is empty; only proven sources are committed")
    say(f"commit_check: {len(ids)} changed source folder(s) in {rng}")
    return findings


def check_pr_text(title: str, body: str, where: str) -> list[str]:
    return text_findings(title or "", f"{where} title") + text_findings(body or "", f"{where} body")


def check_pr_event(path: str) -> list[str]:
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read the event payload {path!r}: {type(exc).__name__}: {exc}"]
    pr = event.get("pull_request") if isinstance(event, dict) else None
    if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
        return ["the event payload has no pull_request with a number; failing closed"]
    return check_pr_text(pr.get("title") or "", pr.get("body") or "", f"pull request {pr['number']}")


def pr_from_api(number: str) -> tuple[str, str]:
    proc = subprocess.run(["gh", "api", f"repos/{REPO_SLUG}/pulls/{number}"], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    data = json.loads(proc.stdout.decode("utf-8"))
    return data.get("title") or "", data.get("body") or ""


# --------------------------------------------------------------------------- selftest

def banned_samples() -> dict[str, str]:
    """Planted texts, assembled at run time so no file holds them."""
    co, sess = "Co-" + "Auth" + "ored-B" + "y", "Cla" + "ude" + "-Session"
    link = "https://" + "claude" + ".ai/code/session_x"
    return {
        "trailer": "Add b\n\n" + co + ": Someone <x@example.com>",
        "trailer variant": "Add b\n\nco\u2011authored\u2011by: x",
        "trailer spaces": "Add b\n\nCo authored by: x",
        "trailer cyrillic": "Add b\n\nC\u043e-Auth" + "ored-By: x",
        "trailer zero-width": "Add b\n\nCo-Auth\u200bored-By: x",
        "session": "Add b\n\n" + sess + ": abc",
        "link": "Add b\n\nsee " + link,
        "link fullwidth": "Add b\n\nsee https://\uff43\uff4c\uff41\uff55\uff44\uff45\uff0e\uff41\uff49/x",
        "link percent": "Add b\n\nsee https://" + "claude" + "%2E" + "ai/x",
        "footer": "Add b\n\n" + "Gener" + "ated" + " with SomeTool",
        "footer italic": "Body\n\n---\n_" + "Gener" + "ated" + " b" + "y [Claude Code](" + link + ")_",
        "footer using": "Add b\n\n" + "Gener" + "ated" + " using " + "Claude",
        "footer robot": "Add b\n\n\U0001F916 " + "Gener" + "ated" + " with [" + "Claude" + " Code]",
        # The first audit's italic probe, byte for byte: underscore right after "with".
        "footer italic audit": "_" + "Gener" + "ated" + " with_ [" + "Cla" + "ude Code](...)",
        "trailer capital cyrillic": "Add b\n\n\u0421o-Auth" + "ored-By: x",
        "trailer capital cyrillic o": "Add b\n\nC\u041e-Auth" + "ored-By: x",
        "session capital greek": "Add b\n\nCl\u0391ude-Session: x",
        "trailer combining mark": "Add b\n\nCo\u0336-Auth" + "ored-By: x",
        "trailer precomposed accent": "Add b\n\nC\u00f3-Auth" + "ored-By: x",
        "trailer hangul filler": "Add b\n\nCo-Auth\u3164ored-By: x",
        "link ideographic dot": "Add b\n\nsee https://" + "claude" + "\u3002" + "ai/x",
        "link halfwidth dot": "Add b\n\nsee https://" + "claude" + "\uff61" + "ai/x",
        "link fullwidth dot": "Add b\n\nsee https://" + "claude" + "\uff0e" + "ai/x",
    }


def selftest() -> list[str]:
    """Build a scratch repository and prove each check fires and clean cases pass."""
    failures: list[str] = []
    samples = banned_samples()
    for label, text in samples.items():
        if not text_findings(text, label):
            failures.append(f"selftest text {label!r}: expected a finding")
        if not check_pr_text(text, "", "selftest"):
            failures.append(f"selftest title {label!r}: expected a finding")
    for label in ("Add the co-author rule", "Writ" + "ten by hand", "Data for the digest"):
        if text_findings(label, "clean"):
            failures.append(f"selftest text {label!r}: expected clean")
    named = ("Commit messages never contain a " + "Claude" + "-Session line, a session or " + "claude" + ".ai link,\n"
             "a \"" + "Gener" + "ated" + " with\" footer, or a " + "Co-" + "Auth" + "ored-By line.\n")
    if content_findings("RULES.md", named.encode()):
        failures.append("selftest: a sentence that names the rules must pass the file check")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        env = dict(os.environ, GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                   GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1],
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        env.pop("BASE_REF", None)

        def run(*args, extra=None):
            subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", *args],
                           cwd=td, env={**env, **(extra or {})}, check=True, capture_output=True)

        def commit(name, data, message, extra=None):
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            run("add", name)
            run("commit", "-q", "-m", message, extra=extra)
            return git("rev-parse", "HEAD", cwd=repo).decode().strip()

        def expect(label, rng, want_hit, branch="job"):
            hits = check_range(rng, branch=branch, cwd=repo)
            if bool(hits) != want_hit:
                failures.append(f"selftest {label}: expected {'a finding' if want_hit else 'clean'}, got {hits}")

        web = {"GIT_COMMITTER_NAME": WEB_COMMITTER[0], "GIT_COMMITTER_EMAIL": WEB_COMMITTER[1]}
        run("init", "-q", "-b", "main")
        base = commit("README.md", b"clean\n", "Add README")
        merged = commit("m.txt", b"x\n", "Merge pull request 1 from the web", extra=web)
        run("update-ref", "refs/remotes/origin/main", merged)
        expect("web-flow committer on main", f"{base}..{merged}", False, branch=None)
        expect("empty range", f"{merged}..{merged}", True)
        last = merged
        clean = commit("a.txt", b"plain text\n", "Add a plain file")
        expect("clean commit", f"{last}..{clean}", False)
        last = clean
        forged = commit("w.txt", b"x\n", "Forged web commit", extra=web)
        expect("web-flow committer off main", f"{last}..{forged}", True)
        last = forged
        for label in ("trailer", "footer", "link fullwidth"):
            sha = commit(f"{label.replace(' ', '-')}.txt", b"x\n", samples[label])
            expect(f"message {label}", f"{last}..{sha}", True)
            last = sha
        cases = (
            ("dash in message", "d.txt", b"x\n", "Add d \u2014 with a dash"),
            ("CR line endings", "e.txt", b"one\r\ntwo\r\n", "Add e"),
            ("forbidden path", "local/f.txt", b"x\n", "Add f"),
            ("data path off the data branch", "digests/p/latest.md", b"x\n", "Add digest"),
            ("non-ASCII Python", "h.py", "x = '\u00e9'\n".encode("utf-8"), "Add h"),
            ("trailer line in a file", "notes.md", samples["trailer"].encode(), "Add notes"),
            ("link in a file", "links.md", samples["link"].encode(), "Add links"),
        )
        for label, name, data, message in cases:
            sha = commit(name, data, message)
            expect(label, f"{last}..{sha}", True)
            last = sha
        stranger = commit("g.txt", b"x\n", "Add g", extra={"GIT_AUTHOR_NAME": "Someone",
                                                           "GIT_AUTHOR_EMAIL": "someone@example.com"})
        expect("wrong author", f"{last}..{stranger}", True)
        footer_merge = commit("n.txt", b"x\n", "Merge pull request 2\n\n" + samples["footer italic"], extra=web)
        run("update-ref", "refs/remotes/origin/main", footer_merge)
        expect("footer in a merge already on main", f"{stranger}..{footer_merge}", True, branch=None)
    if check_pr_text("Job 1c", samples["footer italic"], "pr") == []:
        failures.append("selftest pull request footer: expected a finding")
    if check_pr_text("Job 1c: checks", "A plain description.", "pr"):
        failures.append("selftest clean pull request text: expected clean")
    return failures


# --------------------------------------------------------------------------- entry point

def report(findings: list[str], label: str) -> int:
    for f in findings:
        say(f"commit_check: {f}")
    if findings:
        say(f"commit_check: {label}: {len(findings)} finding(s); fix them before continuing")
        return 1
    say(f"commit_check: {label}: clean")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        say(__doc__.strip())
        return 0 if argv else 2
    mode, rest = argv[0], argv[1:]
    try:
        if mode == "--staged" and not rest:
            return report(check_staged(), "staged")
        if mode == "--message-file" and len(rest) == 1:
            text = Path(rest[0]).read_text(encoding="utf-8", errors="replace")
            return report(check_message_text(text), "commit message")
        if mode == "--pre-push" and len(rest) == 2:
            return report(check_pre_push(rest[0], rest[1], sys.stdin.read().splitlines()), "pre-push")
        if mode == "--main-history" and len(rest) == 1:
            return report(check_range(rest[0], branch="main", main_history=True), "main history")
        if mode in ("--range", "--sources") and len(rest) == 1:
            rng = resolve_range(rest[0])
            if rng is None:
                say(f"commit_check: cannot resolve a range: {base_ref()} and {BOUNDARY_SHA} are both missing "
                    "(fetch with full history); failing closed")
                return 1
            if mode == "--range":
                return report(check_range(rng, branch=current_branch()), "range")
            return report(check_sources(rng), "sources")
        if mode == "--pr" and len(rest) == 1:
            title, body = pr_from_api(rest[0])
            return report(check_pr_text(title, body, f"pull request {rest[0]}"), "pull request")
        if mode == "--pr-event" and len(rest) == 1:
            return report(check_pr_event(rest[0]), "pull request")
        if mode == "--selftest" and not rest:
            return report(selftest(), "selftest")
    except (RuntimeError, OSError, ValueError) as exc:
        say(f"commit_check: could not run: {type(exc).__name__}: {exc}")
        return 1
    say(f"commit_check: unknown arguments {' '.join(argv)!r}; see --help")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
