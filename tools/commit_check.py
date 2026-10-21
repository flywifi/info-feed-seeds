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
  messages   no co-author or session trailer, no session link, no generated footer (a line that
             opens with the generated-with phrase, or that phrase or its by/using forms shortly
             before the assistant's name), no em or en dash. Text is normalised first (NFKC, format characters removed, Unicode hyphens and
             common lookalike letters folded, HTML entities and percent-encoding decoded).
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
SELF_PATHS = ("tools/commit_check.py", "tools/hooks/", ".github/workflows/")

EM_DASH, EN_DASH = "\u2014", "\u2013"
# Every dash-like character a renderer could show as an em or en dash. Checked after HTML entities
# are decoded, because GitHub renders the named HTML entities for both dashes as the characters.
DASHES = (
    ("\u2014", "em dash"), ("\u2013", "en dash"), ("\u2015", "horizontal bar"),
    ("\u2e3a", "two-em dash"), ("\u2e3b", "three-em dash"), ("\ufe58", "small em dash"),
    ("\ufe31", "vertical em dash"),
)

# The banned strings are assembled from pieces so this file never contains what it hunts for.
_CO = "co" + r"[\W_]*" + "author(?:ed)?" + r"[\W_]*" + "by"
_SESSION = "claude" + r"[\W_]*" + "session"
_HOST = "claude" + r"\." + "ai"
_GEN_LINE = r"^[\W_\d]*" + "generated" + r"[\W_]+with(?![a-z0-9])"
_GEN_TOOL = (r"^[\W_\d]*" + "generated" + r"[\W_]+(?:with|by|using)(?![a-z0-9])[\W_]*.{0,24}?"
             + "claude")

MESSAGE_RULES = (
    ("co-author trailer", re.compile(_CO)),
    ("session trailer", re.compile(_SESSION)),
    ("session link", re.compile(_HOST + r"\b")),
    ("generated footer", re.compile(_GEN_LINE, re.M)),
    ("generated footer", re.compile(_GEN_TOOL, re.M)),
)
FILE_RULES = (
    ("co-author trailer line", re.compile(r"^[\W_]*" + _CO + r"[\W_]*:", re.M)),
    ("session trailer line", re.compile(r"^[\W_]*" + _SESSION + r"[\W_]*:", re.M)),
    ("session link", re.compile(r"(?:[a-z][a-z0-9+.-]*:/*|//|www\.|[a-z0-9-]\.)" + _HOST + r"\b|"
                                + _HOST + r"(?=[/:?#])")),
    ("generated footer", re.compile(_GEN_LINE, re.M)),
    ("generated footer", re.compile(_GEN_TOOL, re.M)),
)

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
}.items()}
# Characters that render as nothing but are not format (Cf) characters: Hangul fillers.
_FILLERS = frozenset("\u3164\u115f\u1160\uffa0")
# Dots that IDNA treats as the label separator, so "claude" + ideographic dot + "ai" opens the host.
_DOTS = {ord(c): "." for c in "\u3002\uff61\uff0e"}
_CONFUSABLES = {ord(k): v for k, v in {
    "\u0430": "a", "\u0441": "c", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0443": "y",
    "\u0445": "x", "\u0456": "i", "\u0455": "s", "\u0501": "d", "\u04bb": "h", "\u051d": "w",
    "\u04cf": "l", "\u0261": "g", "\u0131": "i", "\u03b1": "a", "\u03bf": "o", "\u03b5": "e",
    "\u03b9": "i", "\u03bd": "v", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u0251": "a",
}.items()}

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

def normalize(text: str) -> str:
    """Fold the tricks that hide a banned string: entities, percent-encoding, compatibility forms
    (fullwidth letters and dots), format characters (zero-width, bidi), combining marks, Hangul
    fillers, ideographic dots, Unicode hyphens and lookalike letters in either case. Lowercased."""
    value = html.unescape(text or "")
    for _ in range(2):
        value = unquote(value)
    # NFKD, not NFKC: decomposing first means an accented letter (precomposed or built from a
    # combining mark) always leaves a combining mark (Mn) behind, which the next line removes.
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value
                    if unicodedata.category(ch) not in ("Cf", "Mn") and ch not in _FILLERS)
    value = value.translate(_DOTS).translate(_HYPHENS).translate(_CAPITAL_CONFUSABLES)
    return value.lower().translate(_CONFUSABLES)


def _dash_findings(text: str, where: str) -> list[str]:
    decoded = html.unescape(text or "")
    return [f"{where}: contains an {name}" if name[0] in "ae" else f"{where}: contains a {name}"
            for ch, name in DASHES if ch in decoded]


def _rule_findings(text: str, where: str, rules) -> list[str]:
    folded = normalize(text)
    labels = []
    for label, pattern in rules:
        if pattern.search(folded) and label not in labels:
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
                       "(job ranges), or neither a first-parent merge nor GitHub-verified (main history)")
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


def content_findings(path: str, data: bytes) -> list[str]:
    if b"\x00" in data[:8192]:
        return []  # binary
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: not UTF-8 (byte {exc.start})"]
    out = _rule_findings(text, path, FILE_RULES)
    if "\r" in text:
        out.append(f"{path}: has CR line endings")
    decoded = html.unescape(text)
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
    main-history mode it is trusted only on a first-parent merge of the range or on a commit
    GitHub marks verified."""
    shas = git("rev-list", "--reverse", rng, cwd=cwd).decode().split()
    main_ok = git_ok("rev-parse", "--verify", "--quiet", "origin/main^{commit}", cwd=cwd)
    first_parent = set()
    if main_history:
        first_parent = set(git("rev-list", "--first-parent", "--merges", rng, cwd=cwd).decode().split())
    out = []
    for sha in shas:
        raw = git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha, cwd=cwd)
        an, ae, cn, ce, body = raw.decode("utf-8", "replace").split("\x00", 4)
        if main_history:
            web_ok = (cn, ce) == WEB_COMMITTER and (sha in first_parent or verify(sha))
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


def check_range(rng: str, branch: str | None = None, cwd: Path | None = None, main_history: bool = False,
                verify=github_verified) -> list[str]:
    commits = commits_in(rng, cwd=cwd, main_history=main_history, verify=verify)
    if not commits:
        say(f"commit_check: 0 commit(s) in {rng}")
        return [f"{rng}: the range holds no commits; failing closed"]
    findings: list[str] = []
    for c in commits:
        where = f"commit {c['sha'][:7]}"
        findings += text_findings(c["message"], where + " message")
        findings += identity_findings(c["author"], c["committer"], where, on_main=c["on_main"])
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
    co, sess = "Co-" + "Authored-By", "Claude" + "-Session"
    link = "https://" + "claude" + ".ai/code/session_x"
    return {
        "trailer": "Add b\n\n" + co + ": Someone <x@example.com>",
        "trailer variant": "Add b\n\nco\u2011authored\u2011by: x",
        "trailer spaces": "Add b\n\nCo authored by: x",
        "trailer cyrillic": "Add b\n\nC\u043e-Authored-By: x",
        "trailer zero-width": "Add b\n\nCo-Auth\u200bored-By: x",
        "session": "Add b\n\n" + sess + ": abc",
        "link": "Add b\n\nsee " + link,
        "link fullwidth": "Add b\n\nsee https://\uff43\uff4c\uff41\uff55\uff44\uff45\uff0e\uff41\uff49/x",
        "link percent": "Add b\n\nsee https://" + "claude" + "%2E" + "ai/x",
        "footer": "Add b\n\n" + "Gener" + "ated" + " with SomeTool",
        "footer italic": "Body\n\n---\n_" + "Gener" + "ated" + " by [Claude Code](" + link + ")_",
        "footer using": "Add b\n\n" + "Gener" + "ated" + " using " + "Claude",
        "footer robot": "Add b\n\n\U0001F916 " + "Gener" + "ated" + " with [" + "Claude" + " Code]",
        # The first audit's italic probe, byte for byte: underscore right after "with".
        "footer italic audit": "_" + "Gener" + "ated" + " with_ [" + "Cla" + "ude Code](...)",
        "trailer capital cyrillic": "Add b\n\n\u0421o-Authored-By: x",
        "trailer capital cyrillic o": "Add b\n\nC\u041e-Authored-By: x",
        "session capital greek": "Add b\n\nCl\u0391ude-Session: x",
        "trailer combining mark": "Add b\n\nCo\u0336-Authored-By: x",
        "trailer precomposed accent": "Add b\n\nC\u00f3-Authored-By: x",
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
    for label in ("Generated by info-feed-seeds hub poll", "Add the co-author rule", "Written by hand"):
        if text_findings(label, "clean"):
            failures.append(f"selftest text {label!r}: expected clean")
    named = ("Commit messages never contain a " + "Co-" + "Authored-By line, a " + "Claude" + "-Session line,\n"
             "a session or " + "claude" + ".ai link, or a \"" + "Gener" + "ated" + " with\" footer.\n")
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
