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

Two kinds of result
  BLOCK      fixed shapes; any one fails the hook or CI step (exit 1): a co-author or session trailer
             line, a trailer key that names the assistant, a session link or id, a footer line (the
             footer phrase and a tool name on a line of their own), the footer phrase and the
             assistant's name in one sentence, an em or en dash, a review-record token (round,
             finding or ruling numbers, severity tallies), a wrong author or committer, a forbidden
             path, CR line endings and non-ASCII Python. Authorship and credit wording that names an
             AI assistant also blocks: made, written, built or assisted with or by it, thanks or help
             from it, a byline or author label, first-person use, past-tense use for a task, or the
             name as author. The names read are Claude and other assistants and their makers. Public
             text credits no one; where a credit is needed at all it says "the creator". Marks a
             tool leaves without naming itself block too: assistance and session trailer keys and
             session label lines (messages and pull request text), and everywhere an agent mailbox,
             a session link by host and path, a hidden HTML marker and a credit verb with a link to
             a coding tool's host. Pushes are refused for any ref that is not a branch named job/<name>
             or doctor/<fix> (tags, notes and other namespaces included); a deletion of any ref passes.
             Under seeds/ the byline rule reads prose only: a list item, a numbered item, a table row, a
             tag, a quoted string, an object, a URL or a key line holds imported data, whose bylines the
             content rules let stand; every other rule reads every line there. A person's name blocks
             everywhere but an imported seed line; the checker holds neither the name nor its
             fingerprints, which come from IFS_PERSON_FINGERPRINTS or an ignored local file.
  ADVISORY   the footer phrase and the name in one paragraph, and short finding ids. Printed as "ADVISORY:" lines, and as warnings in GitHub Actions; they never
             change the exit code. A person reads them.

Every rule carries a "Limit:" text naming what it deliberately does not read, and
tests/test_commit_check_golden.py pins a case for each limit, so widening a rule is a deliberate,
tested change. Messages and pull request text are checked for any mention of the trailer keys, the
session host and session ids; files only for their real forms (a trailer LINE, a LINK, a footer
LINE), so a sentence that names a rule passes. Text is folded first (NFKD, format characters and
marks removed, Unicode hyphens and lookalike letters folded, HTML entities and every round of
percent-encoding decoded, terminal control sequences removed) and every reading is checked.

Exemptions: tools/commit_exemptions.json lists exact texts in files that one BLOCK rule may skip:
path, rule id, the sha256 of the matched text (printed beside the finding) and a reason of at least
25 characters. Ranges, pushes and staged checks read it from origin/<BASE_REF>, so an entry counts
only once it is on main.

identity   author and committer are flywifi <236871844+flywifi@users.noreply.github.com>. GitHub's
           web-flow committer is accepted only on a commit already on origin/main (it landed through
           GitHub); in main-history mode only on a commit GitHub marks verified.
pushes     the push URL is exactly github.com/flywifi/info-feed-seeds; never to main or data.
ranges     an empty range is a failure (fail closed), except a branch deletion in pre-push.

`--range auto` uses origin/$BASE_REF (default main)..HEAD, falls back to the policy boundary commit
(BOUNDARY_SHA), and fails closed when neither resolves. The checker works from any copy of this file
(CI runs the base branch's copy) and always inspects the repository in the current directory.
Output is ASCII only. Exit 0 clean or advisory only, 1 on any BLOCK finding or error, 2 on a usage
error. Standard library only.
"""
from __future__ import annotations

import bisect
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

# The rules use possessive quantifiers, which need Python 3.11. An older interpreter stops here, which
# fails the hook closed with a reason instead of a regex error.
if sys.version_info < (3, 11):
    sys.exit("commit_check: needs Python 3.11 or later; this is " + sys.version.split()[0])

REPO_SLUG = "flywifi/info-feed-seeds"
BOUNDARY_SHA = "5436f99"
AUTHOR = ("flywifi", "236871844+flywifi@users.noreply.github.com")
WEB_COMMITTER = ("GitHub", "noreply@github.com")
PROTECTED_BRANCHES = ("main", "data")
ZERO_SHA = "0" * 40
SCISSORS = "# ------------------------ >8 ------------------------"
EXEMPTIONS_FILE = "tools/commit_exemptions.json"
MIN_REASON = 25
SELF_PATHS = ("tools/commit_check.py", "tools/hooks/", ".github/workflows/", "tools/main_history_known.txt",
              EXEMPTIONS_FILE, "tests/fixtures/golden_cases.jsonl", "tests/test_commit_check_golden.py",
              "tools/golden_added.py")

# Every dash-like character a renderer could show as an em or en dash. Checked after HTML entities
# are decoded, because GitHub renders the named HTML entities for both dashes as the characters.
DASHES = (
    ("\u2014", "em dash"), ("\u2013", "en dash"), ("\u2015", "horizontal bar"),
    ("\u2e3a", "two-em dash"), ("\u2e3b", "three-em dash"), ("\ufe58", "small em dash"),
    ("\ufe31", "vertical em dash"), ("\ufe32", "vertical en dash"),
)

# --------------------------------------------------------------------------- rules
# The banned strings are assembled from pieces so this file never contains what it hunts for.
# Every quantifier is bounded, and a run of word characters and a run of separators never share a
# character, so no rule backtracks far; the golden test times each rule on 100,000-character lines.

_NAME = "cla" + "ude"
_S = r"[\W_]"
# A line may open with up to 40 symbols, digits or underscores (quote markers, bullets, numbers),
# then a label: a letter, up to 60 letters, digits, spaces or apostrophes, up to 10 symbols, a colon.
_LEAD = r"^(?:[^\w\n]|[_\d]){0,40}"
_LABEL = r"(?:[a-z][a-z0-9 ']{0,60}?[^a-z0-9:\n]{0,10}:(?:[^\w\n]|_){0,10})?"
_CO = "co" + _S + "{0,10}" + "auth" + "or(?:ed)?" + _S + "{0,10}by"
_SESSION = _NAME + _S + "{0,10}" + "sess" + "ion"
_HOST = _NAME + r"\s{0,10}\.\s{0,10}" + "ai"
_PHRASE = "gene" + "rated" + _S + r"{1,40}(?:(?:with|by|using|via)(?![a-z0-9])|w/)"
_TOOL = (r"(?:\[[^\]\n]{1,80}\]|[a-z0-9][\w.'+/-]{0,40}(?:[ \t]{1,3}[a-z0-9][\w.'+/-]{0,40}){0,3})"
         r"(?:\([^()\s]{0,300}\))?")
_ROLE_KEY = r"(?:[a-z][a-z0-9]{0,20}[ -]){0,3}(?:by|assisted|assistant|generated|authors?|authored|ai)"
_SENTENCE_STOP = r"\n\n|[!?]|\.(?![a-z0-9])"
_AUD = "aud" + "it"
_SEV = r"(?:high|medium|med|low|critical)"
# Product tiers that may follow an assistant's name (Claude Code, ChatGPT Plus, Gemini Advanced). Any
# other capitalised word right after the name makes it a different name: Claude Shannon, Gemini
# Observatory, Mistral Press. Rules that use _ENTITY read the text with its case kept, under re.I.
_TIER = (r"(?:code|opus|sonnet|haiku|fable|mythos|instant|pro|plus|flash|ultra|nano|max|mini|turbo|advanced|ai|api|"
         r"assistant|assist|chat|desktop|cli|app|sdk|agent|bot|enterprise|team|edu|gov|live|large|medium|small|"
         r"workspace|studio|x|build|web|autofix|spark|composer|computer|expanse|hub|review|vibe|coder|terminal|"
         r"developer|sonar)")
# In Title Case a making verb or a short function word after the name is not a surname.
_TITLE_WORDS = (r"(?:wrote|written|built|made|created|authored|drafted|coded|produced|generated|helped|designed|"
                r"developed|was|were|is|has|had|and|or|for|to|the|in|on|with|by|from|of|at|as|did|does|do)")
_ENTITY = (r"(?![ \t]{1,3}(?=(?-i:[A-Z][a-z]))(?!(?:" + _TIER + r"|" + _TITLE_WORDS + r")(?![a-z0-9]))"
           r"[a-z]{2,30})")


class _Hit:
    """A match found by a matcher class: the matched text and where it starts, read like a re match."""

    def __init__(self, text: str, start: int = 0):
        self.text, self._start = text, start

    def group(self, _index: int = 0) -> str:
        return self.text

    def start(self) -> int:
        return self._start


class _PhraseThenName:
    """A start pattern, then the name within `reach` characters, in a window that ends at the first
    `stop` match. With `via`, a connective must start in the first `via_reach` characters and the name
    must come after it. Linear: names, stops and connectives are each found once in the whole text,
    and every start looks up the first of each in its window instead of searching a slice."""

    def __init__(self, start: str, reach: int, stop: str, name: str = _NAME, via: str | None = None,
                 via_reach: int = 0, flags: int = 0):
        self.start, self.reach = re.compile(start, flags), reach
        self.stop, self.name = re.compile(stop, flags), re.compile(name, flags)
        self.via, self.via_reach = (re.compile(via, flags) if via else None), via_reach

    def finditer(self, text: str):
        names = [(h.start(), h.end()) for h in self.name.finditer(text)]
        if not names:
            return
        name_starts = [a for a, _ in names]
        stops = [s.start() for s in self.stop.finditer(text)]
        vias = [(v.start(), v.end()) for v in self.via.finditer(text)] if self.via is not None else []
        via_starts = [a for a, _ in vias]
        for m in self.start.finditer(text):
            lo, hi = m.end(), m.end() + self.reach
            i = bisect.bisect_left(stops, lo)
            if i < len(stops) and stops[i] < hi:
                hi = stops[i]
            offset = lo
            if self.via is not None:
                j = bisect.bisect_left(via_starts, lo)
                if j == len(vias) or vias[j][1] > min(lo + self.via_reach, hi):
                    continue
                offset = vias[j][1]
            k = bisect.bisect_left(name_starts, offset)
            if k < len(names) and names[k][1] <= hi:
                yield _Hit(text[m.start():names[k][1]], m.start())

    def search(self, text: str):
        return next(self.finditer(text), None)


# A line that holds imported data rather than prose: a list item, a numbered item, a table row, a tag
# (OPML, HTML; not a comment, which is the author's own note), a quoted string or an object (JSON), a URL,
# or a lowercase key line (YAML; a capitalised label such as Note: is prose). The content rules let a
# byline imported from a public source stand, so under seeds/ the byline and person-name rules do not
# read such lines.
_IMPORTED_LINE = re.compile(r"[ \t]{0,8}(?:[-*+][ \t]|\d{1,3}[.)][ \t]|\||<(?!!--)|\"|[{\[]|https?://|[a-z0-9_-]{1,40}:[ \t])")


class _ProseOnly:
    """A matcher's hits, without those that start on an imported-data line. Line starts are found once
    per text and looked up by bisection, so a long line with many hits stays linear."""

    def __init__(self, inner):
        self.inner = inner

    def finditer(self, text: str):
        starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
        for m in self.inner.finditer(text):
            line_start = starts[bisect.bisect_right(starts, m.start()) - 1]
            if not _IMPORTED_LINE.match(text, line_start):
                yield m

    def search(self, text: str):
        return next(self.finditer(text), None)


# A person's name is never written into public text, this file included. The checker compares the
# SHA-256 of every capitalised 4 to 6 letter word (and of a lowercase account name in a Users\ path)
# against fingerprints it does not publish either: a short digest is brute-forced in seconds, so the
# list lives outside the tree, in the IFS_PERSON_FINGERPRINTS variable (CI takes it from a repository
# secret) or in tools/person_fingerprints.local.json beside this file (ignored; the hooks on a PC).
# The one fingerprint kept here is the public test token the golden set plants.
TEST_TOKEN_FINGERPRINT = "da8eb25c821b5e6df79ed7175974d5a24b90765fa864fa6094fd17170aae3211"
FINGERPRINTS_VAR = "IFS_PERSON_FINGERPRINTS"
FINGERPRINTS_FILE = "person_fingerprints.local.json"
_USERS = r"[Uu][Ss][Ee][Rr][Ss]"
_SHORT_WORD = re.compile(r"(?<![A-Za-z])(?:[A-Z][A-Za-z]{3,5}|(?:(?<=" + _USERS + r"\\)|(?<=" + _USERS + r"\\\\)|(?<="
                         + _USERS + r"/))[a-z]{4,6})(?![A-Za-z])")


def person_fingerprints() -> frozenset:
    """The test token plus every digest the environment or the local file supplies."""
    found = {TEST_TOKEN_FINGERPRINT}
    found.update(x.strip().lower() for x in os.environ.get(FINGERPRINTS_VAR, "").split(",") if x.strip())
    local = Path(__file__).with_name(FINGERPRINTS_FILE)
    if local.exists():
        try:
            data = json.loads(local.read_text(encoding="utf-8-sig"))
            found.update(str(x).strip().lower() for x in data["fingerprints"])
        except (OSError, ValueError, LookupError, TypeError):
            say(f"commit_check: WARNING: {FINGERPRINTS_FILE} is not a JSON object with a \"fingerprints\" list of "
                "hex digests; it supplied nothing")
    return frozenset(x for x in found if re.fullmatch(r"[0-9a-f]{64}", x))


class _Fingerprinted:
    """Words whose SHA-256 is a listed fingerprint. Linear: one hash per candidate word."""

    def finditer(self, text: str):
        listed = person_fingerprints()
        for m in _SHORT_WORD.finditer(text):
            if hashlib.sha256(m.group(0).encode("utf-8")).hexdigest() in listed:
                yield m

    def search(self, text: str):
        return next(self.finditer(text), None)


class Rule:
    """One check. BLOCK rules fail the run; the others print ADVISORY lines. `places` holds "text"
    (commit messages, pull request titles and bodies) and/or "file". `limit` starts with "Limit:"
    and names what the rule deliberately does not read; the golden test pins a case for it."""

    def __init__(self, rid: str, label: str, block: bool, places: tuple, pattern, limit: str,
                 flags: int = 0, cased: bool = False):
        self.id, self.label, self.block, self.places, self.limit, self.cased = rid, label, block, places, limit, cased
        self.matcher = re.compile(pattern, flags) if isinstance(pattern, str) else pattern

    def search(self, text: str):
        return self.matcher.search(text)

    def finditer(self, text: str):
        return self.matcher.finditer(text)


TEXT, FILE, BOTH = ("text",), ("file",), ("text", "file")

BLOCK_RULES = (
    Rule("coauthor-trailer", "co-author trailer", True, BOTH,
         _LEAD + _LABEL + _CO + "(?:" + _S + "{0,10}[:=]|" + _S + "{1,10}" + _NAME + ")",
         "Limit: the key must open a line (after up to 40 symbols or digits and an optional label) and "
         "end with a colon, an equals sign or the assistant's name; a key named mid-sentence in a file "
         "passes.", re.M),
    Rule("coauthor-key", "co-author trailer", True, TEXT, "co" + _S + "{0,10}" + "auth" + "or(?:ed)?-by",
         "Limit: needs a hyphen right before \"by\"; prose such as \"co-authored by Smith\" passes."),
    Rule("coauthor-address", "co-author trailer", True, BOTH,
         _CO + r".{0,60}?<[^<>\s@]{1,64}@[^<>\s]{1,255}>",
         "Limit: the address must follow the key within 60 characters on the same line."),
    Rule("session-trailer", "session trailer", True, FILE,
         _LEAD + _LABEL + _SESSION + _S + "{0,10}[:=]",
         "Limit: in files only a trailer line counts; a sentence naming the key passes.", re.M),
    Rule("session-mention", "session trailer", True, TEXT, _SESSION,
         "Limit: needs the assistant's name and \"session\" within 10 symbols; \"session\" alone passes."),
    Rule("session-link", "session link", True, TEXT, _HOST + r"\b",
         "Limit: the host's dot must be a dot after folding; \"dot ai\" in words, and a longer word "
         "such as \"aim\" after the dot, pass."),
    Rule("session-link-file", "session link", True, FILE,
         r"(?:[a-z0-9+.-]:/{0,10}|//|www\.|[a-z0-9-]\.)" + _HOST + r"\b|" + _HOST + r"(?=[/:?#])",
         "Limit: in files only a link counts (a scheme, //, www., a subdomain or a path after the "
         "host); the bare host in a sentence passes."),
    Rule("session-id", "session id", True, BOTH, r"session_(?:[-_]?[a-z0-9]){20}",
         "Limit: needs 20 letters or digits after \"session_\"; a shorter id passes."),
    Rule("footer-line", "generated footer", True, BOTH,
         _LEAD + _LABEL + _PHRASE + r"(?:[^\w\n]|_){0,40}" + _TOOL + r"(?:[^\w\n]|_){0,40}$",
         "Limit: the line holds only the phrase and a tool of up to four words or a bracketed name, "
         "with an optional link and label; a longer line, or words before the phrase that are not a "
         "label, pass this rule.", re.M),
    Rule("generated-by-assistant", "generated footer", True, BOTH,
         _PhraseThenName(_PHRASE, 200, _SENTENCE_STOP),
         "Limit: the name must follow the phrase in the same sentence within 200 characters; after "
         "\"!\", \"?\", a blank line or a sentence-ending dot it is left to the advisory paragraph rule."),
    Rule("assistant-trailer", "trailer naming the assistant", True, BOTH,
         _LEAD + _ROLE_KEY + r"[^\w\n]{0,3}[:=][^\n]{0,200}?" + _NAME + _ENTITY,
         "Limit: the key must open a line and end in by, assisted, assistant, generated, author, "
         "authors, authored or ai; other keys (\"Note:\", \"Model:\") pass, and so does the name "
         "followed by a capitalised word that is not a product tier (a person who shares the name).",
         re.M | re.I, cased=True),
    Rule("audit-record", "review record", True, BOTH,
         r"(?<![a-z])" + _AUD + r"(?:[ _-]?round)?[ _-]?\d{1,3}(?!\d)"
         r"|(?<![a-z])" + _AUD + r"[ _-]rounds?(?![a-z])"
         r"|(?<![a-z])round[ _-]?\d{1,3}(?![\d.])[ _-]?(?:" + _AUD + r"|findings?|fix(?:es)?|review)"
         r"|(?<![a-z])findings?[ _-]?\d{1,3}(?![\d.])"
         r"|(?<![a-z])ruling[ _-]?\d{1,2}(?!\d)"
         r"|(?<![a-z0-9])\d{1,3} " + _SEV + r"(?![a-z])[,;]? \d{1,3} " + _SEV + r"(?![a-z])",
         "Limit: needs a number right after the review word, or two counts with severity words; "
         "\"the audit log\", a year after the word and a bare F, H, M or L number pass."),
    Rule("audit-severity", "review record", True, BOTH,
         r"(?<![A-Za-z])(?:HIGH|MEDIUM|MED|LOW) ?\d{1,2}(?![\d.])",
         "Limit: capital severity words only; lowercase prose such as \"a high 5\" passes.", cased=True),
    Rule("person-name", "a person's name", True, BOTH, _Fingerprinted(),
         "Limit: a capitalised word of 4 to 6 letters, or a lowercase one right after a Users folder in a path, "
         "whose fingerprint is listed; other spellings, longer words and the name inside a longer word pass, "
         "and without the fingerprint list only the test token is read.", cased=True),
    # Marks a tool leaves without naming itself: trailer keys, mailboxes, links, hidden comments and
    # credit lines with a link. None of them needs a name on the list above.
    Rule("assist-trailer", "assistance trailer", True, TEXT,
         _LEAD + _LABEL + r"(?:(?:ai[\W_]{1,3})?assisted[\W_]{1,3}by|generated[\W_]{1,3}(?:with|by)|"
         r"(?:made|created|written)[\W_]{1,3}with)[^\w\n]{0,3}[:=]",
         "Limit: the key must open a line and end in a colon or an equals sign; prose such as "
         "\"assisted by the new cache\" and files are not read.", re.M),
    Rule("log-trailer", "session or log trailer", True, TEXT,
         _LEAD + r"(?:[A-Z][A-Za-z0-9]{0,30}(?:-[A-Z0-9][A-Za-z0-9]{0,30}){0,4}-(?:Session|Thread|Logs?|Event|"
         r"Screenshot|Publication|Checkpoint)(?:-(?:Id|ID|Url|URL|Type|Created))?|(?:Session|Thread)-(?:Id|ID|Url|URL))"
         r"[ \t]{0,3}[:=]",
         "Limit: the key must open a line, carry at least two title-case words joined by hyphens and end in "
         "one of the listed words (a bare Session-Id or Thread-Url counts too); a single word such as "
         "Event: or a lowercase component prefix such as crawl-log: passes, Run, Task and Build are not such "
         "words, and files (where a configuration key such as max-session is ordinary) are not read.",
         re.M, cased=True),
    Rule("agent-address", "agent mailbox address", True, BOTH,
         r"(?<!github-actions)(?<!dependabot)(?<!renovate)\[bot\]@users\.noreply\.github\.com"
         r"|(?<![a-z0-9])\d{1,12}\+(?:copilot|kiro-agent|junie-agent|openhands-agent|zed-agent|cursoragent)"
         r"@users\.noreply\.github\.com"
         # The mailbox an agent commits from: a machine local part at a coding tool's domain, or a
         # model id (with a digit) at a router's domain.
         r"|(?<![a-z0-9._%+-])(?:noreply|no-reply|agents?|bot|cursoragent|oz-agent|aider|crush|kiro|amp|openhands|"
         r"noemail|roomote|cline|goose|antigravity|codex|copilot|junie|traecli|qwen-coder|grok|vibe|windsurf|"
         r"muse-code)@(?:ampcode\.com|aider\.chat|charm\.land|opencode\.ai|kilo\.ai|kiro\.dev|cursor\.com|warp\.dev|"
         r"all-hands\.dev|swe-agent\.com|codebuff\.com|qoder\.com|codebuddy\.ai|augmentcode\.com|roocode\.com|"
         r"cline\.bot|muse\.dev|block\.github\.com|continue\.dev|anthropic\.com|openai\.com|"
         r"jetbrains\.com|bytedance\.com|alibabacloud\.com|x\.ai|mistral\.ai|moonshot\.cn|codeium\.com|meta\.com|"
         r"gemini\.google\.com|tencent\.com|replit\.com)(?![a-z0-9.-])"
         r"|(?<![a-z0-9._%+-])copilot@github\.com(?![a-z0-9.-])"
         r"|(?<![a-z0-9._%+-])[a-z0-9._-]{0,40}\d[a-z0-9._-]{0,40}@openrouter\.ai(?![a-z0-9.-])"
         r"|(?<=[a-z0-9._%+-])@(?:agents\.local|users\.noreply\.replit\.com)(?![a-z0-9.-])",
         "Limit: a GitHub machine account other than GitHub's own, a machine mailbox (noreply, agent, bot or "
         "a tool's own name) at a coding tool's or vendor's domain, or a model id at a router's domain; a "
         "person's address at those domains (security@, hello@) and a person's noreply address pass."),
    Rule("agent-link", "agent session link", True, BOTH,
         r"(?<![a-z0-9.-])(?:chatgpt\.com/codex/tasks/task_|github\.com/[^/\s]{1,100}/[^/\s]{1,100}/sessions/"
         r"[0-9a-f]{8}-|gh\.io/copilot-coding-agent-tips|cursor\.com/(?:agents/bc-|automations/|"
         r"background-agent\?bcid=|assets/images/(?:open-in-web|view-automation))|app\.devin\.ai/(?:sessions|"
         r"desktop/session)/|static\.devin\.ai/assets/gh-devin-review|jules\.google\.com/task/|ampcode\.com/threads/t-|"
         r"opencode\.ai/s/|social-cards\.sst\.dev/opencode-share/|kilo\.ai/s/|app\.roomote\.dev/(?:task|api/artifacts)/|"
         r"cosmos\.augmentcode\.com/session|storage\.googleapis\.com/screenshot-production-|smith\.langchain\.com/|"
         r"jb\.gg/junie/|www\.cubic\.dev/buttons/)|\.devinenterprise\.com/review/",
         "Limit: a host and the path a tool's session, task, thread or badge link uses; the bare host, "
         "documentation paths and ordinary pull request, issue or feed links pass."),
    Rule("hidden-marker", "hidden agent marker", True, BOTH,
         r"<!--[ \t]{0,10}(?:start copilot|cursor_agent_|junie-(?:bot-comment|feedback)|openclaw[-:]|coderabbit|"
         r"this is an auto-generated (?:description|comment))",
         "Limit: the marker must open an HTML comment; other comments, such as markdownlint directives, pass."),
    Rule("credit-link", "credit line with a tool link", True, BOTH,
         _LEAD + r"(?:made|powered|opened|created|built|written|generated|developed|assisted)[\W_]{1,3}"
         r"(?:with|by|via|using|in|on)[\W_]{1,3}\[[^\]\n]{1,80}\]\((?:[a-z][a-z0-9+.-]{0,10}:)?//(?:[a-z0-9-]{1,63}\.){0,3}"
         r"(?:claude\.ai|claude\.com|anthropic\.com|chatgpt\.com|openai\.com|cursor\.com|cursor\.sh|devin\.ai|"
         r"cognition\.ai|jules\.google(?:\.com)?|ampcode\.com|opencode\.ai|kilo\.ai|roocode\.com|roomote\.dev|"
         r"continue\.dev|augmentcode\.com|kiro\.dev|warp\.dev|openclaw\.ai|all-hands\.dev|openhands\.dev|codeium\.com|"
         r"windsurf\.com|aider\.chat|replit\.com|jb\.gg|jetbrains\.com/junie|qoder\.com|codebuddy\.ai|codebuff\.com|"
         r"charm\.land|mistral\.ai|x\.ai|kimi\.com|moonshot\.cn|qwen\.ai|trae\.ai|manus\.im|genspark\.ai|"
         r"antigravity\.google|gemini\.google\.com|zed\.dev|factory\.ai|copilot\.github\.com|sweep\.dev|cubic\.dev|"
         r"coderabbit\.ai|github\.com/(?:features/copilot|langchain-ai/open-swe|swe-agent|google-gemini|openai/codex|"
         r"anthropics))(?![a-z0-9-])"
         r"|pull request opened by \[|this pull request description was gene" r"rated with",
         "Limit: a making or credit verb, a connective and a Markdown link whose host is a coding tool's, "
         "or two fixed sentences; a link to any other host (\"Powered by [feedparser](github url)\") passes.",
         re.M),
    Rule("session-label", "session label line", True, TEXT,
         _LEAD + r"(?:link to [a-z0-9 ]{1,30}session[ \t]{0,3}:|pr created automatically by|opened on behalf of)"
         r"|\[(?:view (?:the )?(?:[a-z]{1,20} )?(?:team )?(?:session|task|trace)|view thread)\]\(",
         "Limit: the fixed label lines at a line start, and link text that offers to view a session, task, "
         "thread or trace; \"Link to the CI run:\", \"Requested by:\" and a \"[training session]\" link pass.",
         re.M),
)

# Advisory rules. A separator is a run of 1 to 5 characters that are not letters, digits, "!", "?",
# a blank line or a sentence-ending dot; a word is 1 to 30 letters or digits plus up to three
# ".digits" parts (a version such as 4.5). The two never share a character.
_GS = r"(?:[^\w.!?\n]|_|\n(?!\n)|\.(?=[a-z]))"
_GW = r"[a-z0-9]{1,30}+(?:\.[0-9]{1,6}){0,3}+"
# Separator runs and words never share a character, so they are possessive ({1,5}+): a run is taken
# whole and never re-split, which keeps the authorship rules linear without changing a verdict.
_W2 = r"(?:" + _GS + "{1,5}+" + _GW + r"){0,2}?" + _GS + "{1,5}+"
# A separator that stays on the same line.
_LS = r"(?:[^\w.!?\n]|_)"
_AUX = r"(?!(?:is|are|was|were|be|been|being|gets?|got)(?![a-z0-9]))"
# Assistant names the authorship rules read, so the rules hold whichever tool is in use. Claude may be
# glued into a longer token (AnthropicClaude) but not into another name such as Claudette; the other
# names stand as words. Any name may carry a product tier glued on (ClaudeOpus, MistralAI). Names that
# are also ordinary words or given names (a copilot, a codex, a llama, the mistral wind, perplexity,
# grok, windsurfing, Devin, Jules, Junie, Kimi) count only with a capital first letter. GPT followed by
# partition, disk or table is the disk format. The generic phrases (an AI assistant, a coding agent, a
# language model) count like a name, so a product the list does not have is still read when the text
# calls it what it is. The authorship rules read the text with its case kept, under re.I; the
# (?-i:...) parts need the capitals.
_GLUE = r"(?:" + _TIER + r")?(?![a-z])"
# One name is also a common surname. It counts only when no capitalised word of 2 to 13 letters stands
# right before it (a given name makes it a person), which also leaves out a sentence-opening word before it.
_NO_GIVEN_NAME = "".join(r"(?<!(?-i:[A-Z])[a-z]{%d} )" % n for n in range(1, 13))
# Every name starts with one of these letters; the lookahead turns most positions away in one test.
_ASSISTANT = (r"(?=[acdefgijklmnopqrstwxz])(?:" + _NAME + _GLUE
              + r"|(?<![a-z0-9])(?:chat" + _S + r"?gpt|gpts?(?![ \t-]{0,3}(?:partition|disk|table|header|"
              r"entr|label|format|boot|uefi)[a-z]{0,6}(?![a-z]))|openai|anthropic|gemini|deepseek|codellama|llms?|"
              r"qwen|aider|replit|cursor" + _S + r"{1,3}(?:ai|agent|composer)"
              # The generic phrases need an article or determiner, so a topic ("AI agents built on
              # graphs", "search powered by large language models") stays ordinary text.
              r"|(?:an?|the|this|that|our|its|every|each|one)" + _S + r"{1,3}(?:ai" + _S
              + r"{1,3}(?:assistant|model|tool|agent)|(?:coding|ai)" + _S + r"{1,3}agent|(?:large" + _S
              + r"{1,3})?language" + _S + r"{1,3}model)s?"
              # Coined names of coding agents and their makers: words in no language, so any letter case.
              # Closed compounds only: "open code" and "code buddy" stay ordinary.
              r"|opencode|openhands|swe[ -]?agent|roomote|qoder|codebuddy|codebuff|freebuff|openclaw|clawdbot|"
              r"moltbot|clawdis|warelay|clawhub|nanoclaw|agentforce|genspark|codeium|anysphere|z\.ai|"
              r"factory[ -]{1,3}droid|roo[ -]{0,3}code|kilo[ -]{0,3}code|augmentcode)" + _GLUE
              + r"|(?<![a-z0-9])(?:(?-i:C)opilot|(?-i:C)odex|(?-i:L)lama|(?-i:M)istral|(?-i:G)rok"
              r"|(?-i:P)erplexity|(?-i:W)indsurf|(?-i:D)evin|(?-i:J)ules|(?-i:J)unie|(?-i:K)imi"
              r"|(?-i:A)ntigravity|(?-i:K)iro|(?-i:T)rae|(?-i:A)uggie|(?-i:M)anus|(?-i:xAI)|"
              + _NO_GIVEN_NAME + r"(?-i:C)line"
              # Two-word names whose words are ordinary alone, read in title case only.
              r"|(?-i:A)mazon[ \t]{1,3}(?-i:Q)|(?-i:C)ontinue[ \t]{1,3}(?-i:H)ub|(?-i:Z)ed[ \t]{1,3}(?-i:A)gent"
              r"|(?-i:A)ugment[ \t]{1,3}(?-i:C)ode|(?-i:M)use[ \t]{1,3}(?:(?-i:C)ode|(?-i:S)park)"
              r"|(?-i:M)eta[ \t]{1,3}(?-i:AI)|(?-i:A)ll[ \t]{1,3}(?-i:H)ands[ \t]{1,3}(?-i:AI))" + _GLUE
              # These names are ordinary words or given names too: in title case or capitals, any
              # capitalised word after one that is not a tier makes it the ordinary word or a person
              # ("The Copilot Wrote The Flight Log", a novelist who shares a given name).
              + r"(?![ \t]{1,3}(?=(?-i:[A-Z]))(?!" + _TIER + r"(?![a-z0-9]))[a-z]{2,30})" + ")")
# The name directly followed by ".md" is a file such as CLAUDE.md.
_WHO = _ASSISTANT + r"(?!\.md(?![a-z0-9]))" + _ENTITY
# A word allowed between the name and its verb: a product tier, a version, or an adverb.
_MID = (r"(?:" + _TIER + r"|[a-z]?\d{1,3}(?:\.\d{1,3}){0,2}[a-z]?|then|also|just|has|have|had|itself|and|i|we|the|"
        r"[a-z]{2,20}ly)(?![a-z0-9])")
_BOW = r"(?<![a-z0-9])"
# "built-in", "made-up" and "made-to-order" are adjectives, not a claim of making, unless a connective
# follows ("built-in with").
_NOT_ADJ = r"(?!-(?:in|up|to)(?![a-z])(?![ \t-]{1,3}(?:with|by(?![ \t]{1,3}default)|using|via)(?![a-z])))"
_MADE = (_BOW + r"(?:re|co|ghost|hand|over|pre|auto|self|semi|copy|pair" + _S + r"?)?(?:built|made|created|"
         r"written|wrote|authored|produced|coded|developed|drafted|composed|crafted|prepared|designed|"
         r"programmed|assisted|done|generated|opened)(?![a-z])" + _NOT_ADJ)
_VIA = _BOW + r"(?:with|by|using|via|through|alongside|w/)(?![a-z0-9])"

# A sign-off: the name alone on the next line, after up to 10 spaces or tabs and a list or quote mark, with
# up to three tiers or versions and closing punctuation after it.
_SIGNOFF = (_LS + r"{0,5}+\n[ \t]{0,10}+(?:[-*>~]{1,2}[ \t]{0,3})?" + _WHO
            + r"(?:[ \t]{1,3}(?:" + _TIER + r"|[a-z]?\d{1,3}(?:\.\d{1,3}){0,2}[a-z]?)(?![a-z0-9])){0,3}+"
            + r"[^\w\n]{0,5}+(?=\n|\Z)")
# A byline: up to three words, "by", the name.
_BYLINE = (r"[^\w\n]{0,10}(?:[a-z0-9]{1,30}(?:[ \t-]{1,3}|,[ \t]{0,3})(?:and[ \t]{1,3})?){0,3}by" + _S
           + r"{1,10}[a-z0-9]{0,30}?" + _WHO)
_SEED_LIMIT = (" In files under seeds/ a line that starts with a list marker, a number, a table bar, a tag, a "
               "quote, a brace or bracket, a URL or a lowercase key holds imported data and is not read; prose, "
               "an HTML comment and a capitalised label there are read.")
SEEDS_PREFIX = "seeds/"

AUTHORSHIP_RULES = (
    Rule("made-by", "authorship wording (made with or by the assistant)", True, BOTH,
         _PhraseThenName(_MADE, 200, _SENTENCE_STOP, name=_WHO, via=_VIA, via_reach=60, flags=re.I),
         "Limit: the connective must start within 60 characters of the verb and the name must follow "
         "in the same sentence; the name directly followed by \".md\" is the file CLAUDE.md.", cased=True),
    Rule("credit", "authorship wording (credit to the assistant)", True, BOTH,
         r"(?:thanks" + _S + r"{1,5}to|courtesy" + _S + r"{1,5}of|with" + _S + r"{1,5}the" + _S + r"{1,5}help"
         + _S + r"{1,5}of|with" + _S + r"{1,5}help" + _S + r"{1,5}from|powered" + _S + r"{1,5}by|in" + _S
         + r"{1,5}collaboration" + _S + r"{1,5}with)" + _S + r"{1,5}(?:[a-z0-9]{1,30}" + _S + r"{1,5}){0,2}?"
         r"[a-z0-9]{0,30}?" + _WHO + r"|with" + _S + r"{1,5}[a-z0-9]{0,30}?" + _WHO + _S + r"{0,3}s?" + _S
         + r"{1,3}help(?![a-z0-9])"
         r"|(?<![a-z])(?:thanks|thank" + _GS + r"{1,5}+you|kudos)(?:(?:" + _LS + r"|\.(?=[a-z])){1,5}+" + _WHO
         + r"|" + _SIGNOFF + ")"
         r"|(?<![a-z])(?:(?:kudos|credits?)" + _GS + r"{1,5}+to|credits?[ \t]{0,3}[:=])(?:" + _GS + r"{1,5}+" + _WHO
         + r"|" + _SIGNOFF + ")",
         "Limit: at most two words between the credit phrase and the name, and plain thanks or credit "
         "only with the name right after it; \"thanks again to\" passes.", re.I, cased=True),
    Rule("byline", "authorship wording (byline)", True, BOTH,
         r"(?:^|[.;,(]|[ \t]-[ \t])" + _BYLINE,
         "Limit: up to three words between a line start or punctuation and \"by\"; a longer clause, "
         "and identifiers joined by underscores, pass.", re.M | re.I, cased=True),
    Rule("author-label", "authorship wording (author label)", True, BOTH,
         r"auth" + r"or(?:[^\w:=\n]|_){0,5}[:=]" + _S + r"{0,5}" + _WHO,
         "Limit: needs a colon or an equals sign between the label and the name.", re.I, cased=True),
    Rule("first-person", "authorship wording (first person)", True, BOTH,
         _BOW + r"(?:i|we)" + _W2 + r"(?:re|co)?(?:used|use|asked|had|let|leveraged|leverage|utili[sz]ed|employed)"
         + _W2 + _WHO + r"|" + _BOW + r"using" + _GS + r"{1,5}+" + _WHO + _W2 + r"(?:i|we)(?![a-z0-9])",
         "Limit: at most two words on each side of the verb; three words pass.", re.I, cased=True),
    Rule("tool-past", "authorship wording (past-tense use)", True, BOTH,
         _BOW + r"(?:re)?(?:used|asked|had)" + _W2 + _WHO + _W2 + r"to(?![a-z0-9])",
         "Limit: past tense with the verb before the name; present-tense feature text (\"uses ... to\") "
         "passes, and the passive form (\"was used to\") is read by the name-as-author rule.", re.I, cased=True),
    Rule("name-as-author", "authorship wording (the name as author)", True, BOTH,
         _WHO + r"(?:" + _GS + "{1,5}+" + _AUX + _MID + r"){0,2}?" + _GS + r"{1,5}+(?:(?:re|co|ghost|over|auto)?"
         r"(?:wrote|written|built|made|created|authored|drafted|coded|produced|generated|helped|designed|"
         r"developed)(?![a-z0-9])" + _NOT_ADJ + r"|(?:was|were|been|got)" + _GS + r"{1,5}+(?:re)?(?:used|asked|"
         r"leveraged|utili[sz]ed|employed)" + _GS + r"{1,5}+to(?![a-z0-9]))",
         "Limit: at most two words between the name and the verb, each a product tier, a version, an "
         "adverb, \"and\", \"I\", \"we\" or \"the\"; a form of \"be\" only in the passive \"was used to\"; other "
         "words between them (\"the Gemini program developed\") and passive feature text (\"the ... prompt "
         "is built\") pass.",
         re.I, cased=True),
)
ADVISORY_RULES = (
    Rule("generated-near", "authorship wording (footer phrase and name in one paragraph)", False, BOTH,
         _PhraseThenName(_PHRASE, 1000, r"\n\n"),
         "Limit: the same paragraph, within 1,000 characters."),
    Rule("finding-id", "a short finding id", False, BOTH,
         r"\((?:[FHMLN]\d{1,2})(?:, ?[FHMLN]\d{1,2})*\)|(?<![A-Za-z0-9])test_[fhmln]\d{1,2}_",
         "Limit: only a parenthesised list or a test name; a bare F1 passes.", cased=True),
)
BLOCK_RULES = BLOCK_RULES + AUTHORSHIP_RULES
RULES = BLOCK_RULES + ADVISORY_RULES
RULE_IDS = {r.id for r in RULES} | {"dash"}
# Under seeds/ the byline and person-name rules read prose only (see _IMPORTED_LINE), so an imported byline
# may name anyone; every other rule reads every line, so a note of the file's own author ("made with",
# "thanks to") blocks there as anywhere.
SEED_RULES = {r.id: Rule(r.id, r.label, True, FILE, _ProseOnly(r.matcher), r.limit + _SEED_LIMIT, cased=r.cased)
              for r in BLOCK_RULES if r.id in ("byline", "person-name")}
# Branches a clone may push: one per job, or a proposal branch from the doctor.
JOB_BRANCH = re.compile(r"(?:job|doctor)/[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$")

# --------------------------------------------------------------------------- folding

_HYPHENS = {ord(c): "-" for c in "\u00ad\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"}
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
# Dots that IDNA treats as the label separator, so the name, an ideographic dot and "ai" open the host.
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
# For readings that keep the case: the capital of each small lookalike above folds to the Latin capital,
# so a cased reading folds as much as the lowercased one.
_CAPITAL_FROM_SMALL = {ord(chr(k).upper()): v.upper() for k, v in _CONFUSABLES.items()
                       if len(chr(k).upper()) == 1 and chr(k).upper() != chr(k)
                       and ord(chr(k).upper()) not in _CAPITAL_CONFUSABLES}
# Latin small capitals look like capitals, so a cased reading folds them to capitals.
_CAPITAL_FROM_SMALL.update({k: v.upper() for k, v in _CONFUSABLES.items()
                            if unicodedata.name(chr(k), "").startswith("LATIN LETTER SMALL CAPITAL")})

FORBIDDEN_PREFIXES = ("local/", ".cache/", "logs/", ".venv-crawl/")
FORBIDDEN_NAMES = re.compile(r"(?:^|/)(?:[^/]*\.local\.json|\.secrets\.local\.json)$")
DATA_PREFIXES = ("digests/", "state/", "ledger/")
_GITHUB_URL = (re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?github\.com(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$", re.I),
               re.compile(r"^(?:[^@/]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", re.I))
_TAG = re.compile(r"<[^<>\n]*>")
# Terminal control sequences: ECMA-48 CSI (ESC "[" or the C1 byte U+009B, parameters, intermediates,
# a final byte), OSC strings (ESC "]" up to BEL or ESC "\"), two-byte ESC sequences, and the C0 and
# C1 control characters other than tab and newline. They render as nothing.
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]{0,4096}(?:\x07|\x1b\\)|(?:\x1b\[|\x9b)[0-?]{0,64}[ -/]{0,16}[@-~]"
                      r"|\x1b[@-_]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

ADVISED: list[str] = []


def say(message: str = "") -> None:
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def advise(lines: list[str]) -> None:
    """Print advisory lines, and in GitHub Actions a warning annotation for each. Never a failure."""
    for line in lines:
        ADVISED.append(line)
        say(f"commit_check: ADVISORY: {line}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            say("::warning title=commit_check advisory::" + line.replace("%", "%25"))


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

def _decode_layers(text: str) -> list[str]:
    """Every reading of the text: HTML entities decoded once, as a browser renders them, then each
    round of percent-decoding until nothing changes (at most 8 rounds). Line endings become LF."""
    value = html.unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
    layers = [value]
    for _ in range(8):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
        layers.append(value)
    return layers


def _views(text: str) -> list[str]:
    """Each decoding layer, and each layer with control sequences and characters removed."""
    out = []
    for layer in _decode_layers(text):
        for view in (layer, _CONTROLS.sub("", _ESCAPES.sub("", layer))):
            if view not in out:
                out.append(view)
    return out


def _fold(value: str, keep_case: bool = False) -> str:
    # NFKD, not NFKC: decomposing first means an accented letter always leaves a combining mark (Mn)
    # behind, which the next line removes.
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value
                    if unicodedata.category(ch) not in ("Cf", "Mn", "Me") and ch not in _FILLERS)
    value = value.translate(_DOTS).translate(_HYPHENS).translate(_CAPITAL_CONFUSABLES)
    if keep_case:
        return value.translate(_CAPITAL_FROM_SMALL).translate(_CONFUSABLES)
    return value.lower().translate(_CONFUSABLES)


def match_sha256(matched: str) -> str:
    return hashlib.sha256(matched.encode("utf-8")).hexdigest()


def _scan(text: str, rules, exempt: frozenset = frozenset()) -> list[tuple]:
    """(rule, matched text) for each rule that matches any reading of the text, with or without HTML
    tags. A match whose (rule id, sha256) is in `exempt` does not count."""
    found: dict[str, tuple] = {}
    for view in _views(text):
        folded: dict[bool, tuple] = {}
        for rule in rules:
            if rule.id in found:
                continue
            if rule.cased not in folded:
                f = _fold(view, keep_case=rule.cased)
                folded[rule.cased] = tuple(dict.fromkeys((f, _TAG.sub("", f))))
            for reading in folded[rule.cased]:
                hit = None
                if not exempt:
                    m = rule.search(reading)
                    hit = m.group(0) if m else None
                else:
                    for m in rule.finditer(reading):
                        if (rule.id, match_sha256(m.group(0))) not in exempt:
                            hit = m.group(0)
                            break
                if hit is not None:
                    found[rule.id] = (rule, hit)
                    break
    return list(found.values())


def _rules(block: bool, place: str) -> tuple:
    return tuple(r for r in RULES if r.block == block and place in r.places)


def _file_rules(path: str) -> tuple:
    """The BLOCK rules for one file; under seeds/ the byline rule leaves imported-data lines alone."""
    rules = _rules(True, "file")
    if path.startswith(SEEDS_PREFIX):
        rules = tuple(SEED_RULES.get(r.id, r) for r in rules)
    return rules


def _dash_hits(text: str) -> list[tuple[str, str, int]]:
    """Each dash found in any view, with its line in the first view that shows it."""
    views = _views(text)
    hits = []
    for ch, name in DASHES:
        for view in views:
            if ch in view:
                hits.append((ch, name, view[: view.index(ch)].count("\n") + 1))
                break
    return hits


def _an(name: str) -> str:
    return "an" if name[0] in "aeiou" else "a"


def text_findings(text: str, where: str) -> list[str]:
    """Commit messages, pull request titles and bodies: BLOCK findings."""
    out = [f"{where}: contains {_an(r.label)} {r.label} [{r.id}]" for r, _ in _scan(text, _rules(True, "text"))]
    return out + [f"{where}: contains {_an(name)} {name} [dash]" for _, name, _ in _dash_hits(text)]


def text_advisories(text: str, where: str) -> list[str]:
    return [f"{where}: {r.label} [{r.id}]" for r, _ in _scan(text, _rules(False, "text"))]


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
    # Any letter case: Windows and macOS treat "Logs/" and "logs/" as one folder.
    if path.lower().startswith(FORBIDDEN_PREFIXES) or FORBIDDEN_NAMES.search(path.lower()):
        out.append(f"{path}: this path is never tracked")
    if path.lower().startswith(DATA_PREFIXES) and branch != "data":
        out.append(f"{path}: machine output belongs only on the data branch")
    return out


def file_text(data: bytes) -> tuple[str | None, str | None]:
    """(text, problem). A NUL byte in the first 8 KiB marks a binary file by git's test; its text
    rules still run on a decoded copy with NUL removed, so a NUL cannot hide text."""
    if b"\x00" in data[:8192]:
        return data.decode("utf-8", "replace").replace("\x00", ""), None
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, f"not UTF-8 (byte {exc.start})"


def content_findings(path: str, data: bytes, exemptions=None) -> list[str]:
    """BLOCK findings in one file. `exemptions` is the entry list from load_exemptions."""
    text, problem = file_text(data)
    if text is None:
        return [f"{path}: {problem}"]
    exempt = frozenset((e["rule"], e["match_sha256"]) for e in (exemptions or ()) if e["path"] == path)
    out = [f"{path}: contains {_an(r.label)} {r.label} [{r.id} {match_sha256(m)}]"
           for r, m in _scan(text, _file_rules(path), exempt)]
    if b"\x00" in data[:8192]:
        return out
    if "\r" in text:
        out.append(f"{path}: has CR line endings")
    for _, name, line in _dash_hits(text):
        out.append(f"{path}:{line}: contains {_an(name)} {name} [dash]")
    if path.endswith(".py"):
        for index, ch in enumerate(text):
            if ord(ch) > 127:
                line = text[:index].count("\n") + 1
                out.append(f"{path}:{line}: non-ASCII character in a Python file (use \\u escapes)")
                break
    return out


def content_advisories(path: str, data: bytes) -> list[str]:
    text, _ = file_text(data)
    return [] if text is None else [f"{path}: {r.label} [{r.id}]" for r, _ in _scan(text, _rules(False, "file"))]


def file_matches(text: str, path: str = "") -> list[tuple[str, str]]:
    """Every (rule id, sha256 of the matched text) a BLOCK file rule finds: what an exemption may name."""
    out = set()
    for view in _views(text):
        for rule in _file_rules(path):
            f = _fold(view, keep_case=rule.cased)
            for reading in dict.fromkeys((f, _TAG.sub("", f))):
                out.update((rule.id, match_sha256(m.group(0))) for m in rule.finditer(reading))
    return sorted(out)


def load_exemptions(raw: str | None) -> tuple[list[dict], list[str]]:
    """Parse the exemptions file: (entries, problems). None (no file) means no exemptions."""
    if raw is None:
        return [], []
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return [], [f"{EXEMPTIONS_FILE}: not JSON ({exc})"]
    if not isinstance(data, dict) or not isinstance(data.get("exemptions"), list):
        return [], [f"{EXEMPTIONS_FILE}: needs an \"exemptions\" list"]
    entries, problems, seen = [], [], set()
    for n, e in enumerate(data["exemptions"]):
        where = f"{EXEMPTIONS_FILE} entry {n}"
        if not isinstance(e, dict) or set(e) != {"path", "rule", "match_sha256", "reason"}:
            problems.append(f"{where}: needs exactly path, rule, match_sha256 and reason")
            continue
        key = (e["path"], e["rule"], e["match_sha256"])
        if not any(r.id == e["rule"] for r in _rules(True, "file")):
            problems.append(f"{where}: {e['rule']!r} is not a BLOCK rule for files")
        elif not re.fullmatch(r"[0-9a-f]{64}", str(e["match_sha256"])):
            problems.append(f"{where}: match_sha256 must be 64 lowercase hex characters")
        elif len(str(e["reason"]).strip()) < MIN_REASON:
            problems.append(f"{where}: needs a written reason of {MIN_REASON}+ characters")
        elif key in seen:
            problems.append(f"{where}: listed more than once")
        else:
            seen.add(key)
            entries.append(e)
    return entries, problems


def verdict(text: str, place: str) -> str:
    """"block", "advisory" or "pass" for a text as a commit message, a pull request title or body, a
    file, or a file under seeds/. The golden test compares it with each case."""
    if place in ("file", "seed"):
        path = "seeds/golden.md" if place == "seed" else "golden.md"
        data = (text + "\n").encode("utf-8")
        if content_findings(path, data):
            return "block"
        return "advisory" if content_advisories(path, data) else "pass"
    kept = text.split(SCISSORS, 1)[0] if place == "message" else text
    if text_findings(kept, place):
        return "block"
    return "advisory" if text_advisories(kept, place) else "pass"


# --------------------------------------------------------------------------- ranges

def base_ref() -> str:
    return f"origin/{os.environ.get('BASE_REF') or 'main'}"


def base_exemptions(cwd: Path | None = None) -> tuple[list[dict], list[str]]:
    """The exemptions on origin/<BASE_REF>, so a change cannot exempt its own text."""
    try:
        raw = git("show", f"{base_ref()}:{EXEMPTIONS_FILE}", cwd=cwd).decode("utf-8")
    except RuntimeError:
        raw = None
    return load_exemptions(raw)


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
    exemptions, problems = base_exemptions(cwd)
    findings += problems
    for c in commits:
        where = f"commit {c['sha'][:7]}"
        mine = identity_findings(c["author"], c["committer"], where, on_main=c["on_main"])
        mine = text_findings(c["message"], where + " message") + mine
        advise(text_advisories(c["message"], where + " message"))
        if mine and c["sha"] in known:
            for f in mine:
                say(f"commit_check: known, acknowledged in {KNOWN_FILE} ({known[c['sha']]}): {f}")
            continue
        findings += mine
    tip, paths = changed_paths(rng, cwd=cwd)
    for path in paths:
        findings += path_findings(path, branch)
        data = git("show", f"{tip}:{path}", cwd=cwd)
        findings += content_findings(path, data, exemptions)
        advise(content_advisories(path, data))
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
    exemptions, problems = base_exemptions(cwd)
    findings += problems
    raw = git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=d", cwd=cwd)
    paths = [p for p in raw.decode("utf-8", "replace").split("\x00") if p]
    branch = current_branch(cwd)
    for path in paths:
        findings += path_findings(path, branch)
        data = git("show", f":{path}", cwd=cwd)
        findings += content_findings(path, data, exemptions)
        advise(content_advisories(path, data))
    say(f"commit_check: {len(paths)} staged file(s)")
    return findings


def check_message_text(text: str) -> list[str]:
    """Everything above git's scissors line is checked, comment lines included: whether git strips
    them depends on the cleanup mode, so a banned line hidden behind '#' is still refused."""
    kept = text.split(SCISSORS, 1)[0]
    advise(text_advisories(kept, "commit message"))
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
            continue  # deleting a branch, a tag or a stray ref: the one empty push allowed
        if not remote_ref.startswith("refs/heads/"):
            # A tag carries a tagger and a message of its own that no check here reads, and notes and
            # other namespaces are where some tools keep attribution; the maintainer tags on GitHub.
            findings.append(f"refusing to push {remote_ref}: only job/<name> and doctor/<fix> branches are "
                            "pushed from a clone (tags are made by the maintainer on GitHub)")
            continue
        if not JOB_BRANCH.match(target):
            findings.append(f"refusing to push branch {target}: a branch is named job/<name> or doctor/<fix>")
            continue
        # The range CI checks (--range auto): every commit on the branch since origin/<BASE_REF>, not
        # only the commits this push adds, so a branch CI would refuse is refused here first.
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
    advise(text_advisories(title or "", f"{where} title") + text_advisories(body or "", f"{where} body"))
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
        # The italic footer with an underscore right after the connective, byte for byte.
        "footer italic underscore": "_" + "Gener" + "ated" + " with_ [" + "Cla" + "ude Code](...)",
        "trailer capital cyrillic": "Add b\n\n\u0421o-Auth" + "ored-By: x",
        "trailer capital cyrillic o": "Add b\n\nC\u041e-Auth" + "ored-By: x",
        "session capital greek": "Add b\n\nCl\u0391ude-Session: x",
        "trailer combining mark": "Add b\n\nCo\u0336-Auth" + "ored-By: x",
        "trailer accented letter": "Add b\n\nC\u00f3-Auth" + "ored-By: x",
        "trailer hangul filler": "Add b\n\nCo-Auth\u3164ored-By: x",
        "link ideographic dot": "Add b\n\nsee https://" + "claude" + "\u3002" + "ai/x",
        "link halfwidth dot": "Add b\n\nsee https://" + "claude" + "\uff61" + "ai/x",
        "link fullwidth dot": "Add b\n\nsee https://" + "claude" + "\uff0e" + "ai/x",
        "assist trailer": "Add b\n\nAssisted-by: SomeTool",
        "log trailer": "Add b\n\nAgent-Logs-Url: https://example.com/x",
        "agent address": "Add b\n\nreviewed by tool[bot]@users." + "noreply.github.com",
        "agent link": "Add b\n\nsee https://jules.google.com" + "/task/1",
        "hidden marker": "Add b\n\n<!-- START" + " COPILOT CODING AGENT TIPS -->",
        "credit link": "Add b\n\nMade with [SomeTool](https://cursor" + ".com)",
        "session label": "Add b\n\n[view thread](https://example.com/t/1)",
    }


def git_local_env_names() -> set[str]:
    """Variables that point git at one repository (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and the rest).
    A hook run in a linked worktree receives GIT_DIR, so scratch repositories must never inherit them."""
    proc = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=False)
    names = set(proc.stdout.split()) if proc.returncode == 0 else set()
    return names | {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                    "GIT_PREFIX"}


def selftest() -> list[str]:
    """Build a scratch repository and prove each check fires and clean cases pass. Variables that point
    git at a repository are removed for the duration, so the scratch work cannot reach another one."""
    saved = {name: os.environ.pop(name) for name in git_local_env_names() if name in os.environ}
    try:
        return _selftest()
    finally:
        os.environ.update(saved)


def _selftest() -> list[str]:
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
    say(f"commit_check: {label}: clean" + (f" ({len(ADVISED)} advisory line(s) above)" if ADVISED else ""))
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        say(__doc__.strip())
        return 0 if argv else 2
    mode, rest = argv[0], argv[1:]
    if os.environ.get("GITHUB_ACTIONS") == "true" and len(person_fingerprints()) == 1:
        say(f"::warning title=commit_check::{FINGERPRINTS_VAR} supplied no digest (one line of comma-separated "
            "hex); the person-name rule reads only the test token in this run")
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
