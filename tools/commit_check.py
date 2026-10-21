#!/usr/bin/env python3
"""Commit hygiene for info-feed-seeds: the checks every commit, push and pull request must pass.

Usage:
  python3 tools/commit_check.py --staged                 pre-commit: staged files and the author
  python3 tools/commit_check.py --message-file FILE      commit-msg: one commit message
  python3 tools/commit_check.py --pre-push REMOTE URL    pre-push: reads git's ref lines on stdin
  python3 tools/commit_check.py --range A..B | auto      CI: every commit and changed file in a range
  python3 tools/commit_check.py --sources A..B | auto    CI: proof records of changed source folders
  python3 tools/commit_check.py --pr N                   a pull request's title and body (gh api, REST)
  python3 tools/commit_check.py --pr-event FILE          the same, from a GitHub Actions event payload
  python3 tools/commit_check.py --selftest               prove the checks fire, in a scratch repository

Checks
  messages   no co-author trailer, no session trailer, no claude.ai link, no "generated with/by"
             Claude footer, no em or en dash
  identity   author is flywifi <236871844+flywifi@users.noreply.github.com>; the committer is the
             same, or GitHub <noreply@github.com> for commits made on github.com (web merges)
  files      UTF-8, no CR line endings, no em or en dash, ASCII-only .py files; never local/, .cache/,
             logs/, .venv-crawl/, *.local.json or .secrets.local.json; never digests/, state/ or
             ledger/ outside the data branch
  pushes     the push URL names flywifi/info-feed-seeds; never to main or data

`--range auto` uses origin/$BASE_REF (default main)..HEAD, falls back to the policy boundary commit
(BOUNDARY_SHA) and fails closed when neither resolves. Output is ASCII only. Exit 0 clean, 1 on any
finding, 2 on a usage error. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_SLUG = "flywifi/info-feed-seeds"
BOUNDARY_SHA = "5436f99"
AUTHOR = ("flywifi", "236871844+flywifi@users.noreply.github.com")
WEB_COMMITTER = ("GitHub", "noreply@github.com")
PROTECTED_BRANCHES = ("main", "data")
ZERO_SHA = "0" * 40

EM_DASH, EN_DASH = "\u2014", "\u2013"
BANNED_TEXT = (
    ("co-author trailer", re.compile(r"co-authored-by", re.I)),
    ("session trailer", re.compile(r"claude-session", re.I)),
    ("claude.ai link", re.compile(r"claude\.ai\b", re.I)),
    ("generated-by footer", re.compile(r"generated\s+(?:with|by)\W{0,4}claude", re.I)),
)
FORBIDDEN_PREFIXES = ("local/", ".cache/", "logs/", ".venv-crawl/")
FORBIDDEN_NAMES = re.compile(r"(?:^|/)(?:[^/]*\.local\.json|\.secrets\.local\.json)$")
DATA_PREFIXES = ("digests/", "state/", "ledger/")


def say(message: str = "") -> None:
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def git(*args: str, cwd: Path | None = None, input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=str(cwd or ROOT), input=input_bytes,
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def git_ok(*args: str, cwd: Path | None = None) -> bool:
    return subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True).returncode == 0


# --------------------------------------------------------------------------- text checks

def text_findings(text: str, where: str) -> list[str]:
    """Banned footer and trailer text, and dashes, in a message, title or body."""
    out = []
    for label, pattern in BANNED_TEXT:
        if pattern.search(text or ""):
            out.append(f"{where}: contains a {label}")
    for dash, name in ((EM_DASH, "em dash"), (EN_DASH, "en dash")):
        if dash in (text or ""):
            out.append(f"{where}: contains an {name}")
    return out


def identity_findings(author: tuple[str, str], committer: tuple[str, str] | None, where: str) -> list[str]:
    out = []
    if tuple(author) != AUTHOR:
        out.append(f"{where}: author is {author[0]} <{author[1]}>, expected {AUTHOR[0]} <{AUTHOR[1]}>")
    if committer is not None and tuple(committer) not in (AUTHOR, WEB_COMMITTER):
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
    out = []
    if "\r" in text:
        out.append(f"{path}: has CR line endings")
    for dash, name in ((EM_DASH, "em dash"), (EN_DASH, "en dash")):
        if dash in text:
            line = text[: text.index(dash)].count("\n") + 1
            out.append(f"{path}:{line}: contains an {name}")
    if path.endswith(".py"):
        for index, ch in enumerate(text):
            if ord(ch) > 127:
                line = text[:index].count("\n") + 1
                out.append(f"{path}:{line}: non-ASCII character in a Python file (use \\u escapes)")
                break
    return out


# --------------------------------------------------------------------------- ranges

def resolve_range(spec: str, cwd: Path | None = None) -> str | None:
    """'auto' -> origin/<BASE_REF>..HEAD, else BOUNDARY..HEAD, else None (fail closed)."""
    if spec != "auto":
        return spec
    base = f"origin/{os.environ.get('BASE_REF') or 'main'}"
    if git_ok("rev-parse", "--verify", "--quiet", base + "^{commit}", cwd=cwd):
        return f"{base}..HEAD"
    if git_ok("rev-parse", "--verify", "--quiet", BOUNDARY_SHA + "^{commit}", cwd=cwd):
        return f"{BOUNDARY_SHA}..HEAD"
    return None


def commits_in(rng: str, cwd: Path | None = None) -> list[dict]:
    raw = git("log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x1e", rng, cwd=cwd)
    out = []
    for record in raw.decode("utf-8", "replace").split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, an, ae, cn, ce, body = record.split("\x00", 5)
        out.append({"sha": sha, "author": (an, ae), "committer": (cn, ce), "message": body})
    return out


def changed_paths(rng: str, cwd: Path | None = None) -> tuple[str, list[str]]:
    """(tip, paths added or modified between the range's base and tip)."""
    start, _, tip = rng.partition("..")
    tip = tip or "HEAD"
    raw = git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", f"{start}...{tip}", cwd=cwd)
    return tip, [p for p in raw.decode("utf-8", "replace").split("\x00") if p]


def check_range(rng: str, branch: str | None = None, cwd: Path | None = None) -> list[str]:
    findings: list[str] = []
    commits = commits_in(rng, cwd=cwd)
    for c in commits:
        where = f"commit {c['sha'][:7]}"
        findings += text_findings(c["message"], where + " message")
        findings += identity_findings(c["author"], c["committer"], where)
    tip, paths = changed_paths(rng, cwd=cwd)
    for path in paths:
        findings += path_findings(path, branch)
        findings += content_findings(path, git("show", f"{tip}:{path}", cwd=cwd))
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
    ident = git("var", "GIT_AUTHOR_IDENT", cwd=cwd).decode("utf-8", "replace")
    m = re.match(r"^(.*) <([^>]*)>", ident)
    if m:
        findings += identity_findings((m.group(1), m.group(2)), None, "author for this commit")
    raw = git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=d", cwd=cwd)
    paths = [p for p in raw.decode("utf-8", "replace").split("\x00") if p]
    branch = current_branch(cwd)
    for path in paths:
        findings += path_findings(path, branch)
        findings += content_findings(path, git("show", f":{path}", cwd=cwd))
    say(f"commit_check: {len(paths)} staged file(s)")
    return findings


def check_message_file(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    return text_findings("\n".join(lines), "commit message")


def check_pre_push(remote: str, url: str, lines: list[str], cwd: Path | None = None) -> list[str]:
    findings: list[str] = []
    if REPO_SLUG not in (url or ""):
        findings.append(f"push URL {url!r} does not name {REPO_SLUG}")
    try:
        push_url = git("remote", "get-url", "--push", "origin", cwd=cwd).decode().strip()
        if REPO_SLUG not in push_url:
            findings.append(f"origin push URL {push_url!r} does not name {REPO_SLUG}")
    except RuntimeError as exc:
        findings.append(str(exc))
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        target = remote_ref.removeprefix("refs/heads/")
        if target in PROTECTED_BRANCHES:
            findings.append(f"refusing to push to {target}: changes reach {target} only through pull "
                            "requests (main) or the poll workflow (data)")
            continue
        if local_sha == ZERO_SHA:
            continue  # deleting a job branch
        if remote_sha != ZERO_SHA and git_ok("cat-file", "-e", remote_sha + "^{commit}", cwd=cwd):
            rng = f"{remote_sha}..{local_sha}"
        else:
            base = resolve_range("auto", cwd=cwd)
            if base is None:
                findings.append("no base to compare against (origin/main and the boundary are both missing)")
                continue
            rng = base.replace("HEAD", local_sha)
        findings += check_range(rng, branch=target, cwd=cwd)
    return findings


def check_sources(rng: str, cwd: Path | None = None) -> list[str]:
    """Every added or changed sources/<id>/ folder carries a live proof with a sample item.
    Stands in for `hub check --prove` until that command exists."""
    root = Path(cwd or ROOT)
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


def pr_from_api(number: str) -> tuple[str, str]:
    proc = subprocess.run(["gh", "api", f"repos/{REPO_SLUG}/pulls/{number}"], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    data = json.loads(proc.stdout.decode("utf-8"))
    return data.get("title") or "", data.get("body") or ""


# --------------------------------------------------------------------------- selftest

def selftest() -> list[str]:
    """Build a scratch repository and prove each check fires and a clean commit passes."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        env = dict(os.environ, GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                   GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1],
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

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

        run("init", "-q", "-b", "main")
        base = commit("README.md", b"clean\n", "Add README")

        def expect(label, sha_range, want_hit):
            hits = check_range(sha_range, branch="job", cwd=repo)
            if bool(hits) != want_hit:
                failures.append(f"selftest {label}: expected {'a finding' if want_hit else 'clean'}, got {hits}")

        clean = commit("a.txt", b"plain text\n", "Add a plain file")
        expect("clean commit", f"{base}..{clean}", False)
        trailer = commit("b.txt", b"x\n", "Add b\n\nCo-Authored-By: Someone <x@example.com>")
        expect("co-author trailer", f"{clean}..{trailer}", True)
        footer = commit("c.txt", b"x\n", "Add c\n\nGenerated with [Claude Code]")
        expect("generated footer", f"{trailer}..{footer}", True)
        dash = commit("d.txt", b"x\n", "Add d \u2014 with a dash")
        expect("em dash in message", f"{footer}..{dash}", True)
        crlf = commit("e.txt", b"one\r\ntwo\r\n", "Add e")
        expect("CR line endings", f"{dash}..{crlf}", True)
        forbidden = commit("local/f.txt", b"x\n", "Add f")
        expect("forbidden path", f"{crlf}..{forbidden}", True)
        data = commit("digests/p/latest.md", b"x\n", "Add digest")
        expect("data path off the data branch", f"{forbidden}..{data}", True)
        stranger = commit("g.txt", b"x\n", "Add g", extra={"GIT_AUTHOR_NAME": "Someone",
                                                           "GIT_AUTHOR_EMAIL": "someone@example.com"})
        expect("wrong author", f"{data}..{stranger}", True)
        py = commit("h.py", "x = '\u00e9'\n".encode("utf-8"), "Add h")
        expect("non-ASCII Python", f"{stranger}..{py}", True)
        web = commit("i.txt", b"x\n", "Merge from the web", extra={"GIT_COMMITTER_NAME": WEB_COMMITTER[0],
                                                                   "GIT_COMMITTER_EMAIL": WEB_COMMITTER[1]})
        expect("web-flow committer", f"{py}..{web}", False)
        if not check_pr_text("Job 1c", "Body\n\n---\n_Generated by [Claude Code](https://claude.ai/code/x)_", "pr"):
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
        say(__doc__.strip().splitlines()[2] if argv else __doc__.strip())
        return 2 if not argv else 0
    mode, rest = argv[0], argv[1:]
    try:
        if mode == "--staged" and not rest:
            return report(check_staged(), "staged")
        if mode == "--message-file" and len(rest) == 1:
            return report(check_message_file(rest[0]), "commit message")
        if mode == "--pre-push" and len(rest) == 2:
            return report(check_pre_push(rest[0], rest[1], sys.stdin.read().splitlines()), "pre-push")
        if mode in ("--range", "--sources") and len(rest) == 1:
            rng = resolve_range(rest[0])
            if rng is None:
                say(f"commit_check: cannot resolve a range: origin/main and {BOUNDARY_SHA} are both missing "
                    "(fetch with full history); failing closed")
                return 1
            if mode == "--range":
                return report(check_range(rng, branch=current_branch()), "range")
            return report(check_sources(rng), "sources")
        if mode == "--pr" and len(rest) == 1:
            title, body = pr_from_api(rest[0])
            return report(check_pr_text(title, body, f"pull request {rest[0]}"), "pull request")
        if mode == "--pr-event" and len(rest) == 1:
            event = json.loads(Path(rest[0]).read_text(encoding="utf-8"))
            pr = event.get("pull_request") or {}
            return report(check_pr_text(pr.get("title") or "", pr.get("body") or "",
                                        f"pull request {pr.get('number')}"), "pull request")
        if mode == "--selftest" and not rest:
            return report(selftest(), "selftest")
    except RuntimeError as exc:
        say(f"commit_check: {exc}")
        return 1
    say(f"commit_check: unknown arguments {' '.join(argv)!r}; see --help")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
