"""The refused and allowed corpora: what every rule change must keep true.

tests/fixtures/refused_corpus.jsonl holds every string an audit proved must be refused, and
tests/fixtures/allowed_corpus.jsonl holds text that must pass (prose about the rules, real commit
messages and pull request bodies). tests/fixtures/pending_ruling.jsonl holds text whose verdict
waits on a ruling (finding 7: any "Generated with" footer, or only ones that name the assistant);
its current verdicts are printed, never asserted. Each line is {"id", "source", "exact", "where",
"b64"}; the text is base64 so no scanner ever sees the literals. "exact" says whether the audit
gave the string byte for byte or it was reconstructed from the audit's description.

A rule change is acceptable only when both corpora pass. The corpora only grow:
tests/fixtures/corpus.lock pins every id to the sha256 of its text and its kind, and an id that
exists on origin/main must still exist here."""
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import commit_check as cc  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
CORPORA = {"refused": FIXTURES / "refused_corpus.jsonl", "allowed": FIXTURES / "allowed_corpus.jsonl"}
PENDING = FIXTURES / "pending_ruling.jsonl"
LOCK = FIXTURES / "corpus.lock"
ALL = dict(CORPORA, pending=PENDING)
PLACES = {"message", "body", "file", "main-history"}
SOURCES = {"audit1", "audit2", "audit3", "audit4", "audit5", "audit6", "audit7", "audit8", "audit9", "builder", "repo", "ruling"}


def load(path_or_text) -> list[dict]:
    text = path_or_text.read_text(encoding="utf-8") if isinstance(path_or_text, Path) else path_or_text
    out = []
    for line in text.splitlines():
        if line.strip():
            entry = json.loads(line)
            entry["text"] = base64.b64decode(entry["b64"]).decode("utf-8")
            out.append(entry)
    return out


def refused_in(place: str, text: str, checker=cc) -> bool:
    if place == "message":
        return bool(checker.text_findings(text, "m"))
    if place == "body":
        return bool(checker.check_pr_text("Title", text, "pr"))
    if place == "title":
        return bool(checker.check_pr_text(text, "", "pr"))
    if place == "file":
        return bool(checker.content_findings("corpus.md", (text + "\n").encode("utf-8")))
    raise ValueError(place)


def checked_places(e: dict) -> list[str]:
    """The listed places other than main-history, plus a pull request title wherever a message or
    a body is listed (audit 7, HIGH 1)."""
    places = [p for p in e["where"] if p != "main-history"]
    return places + (["title"] if {"message", "body"} & set(places) else [])


def pending_passes(entries: list[dict]) -> list[str]:
    """Every place where the current checker lets a pending entry through."""
    passed = [f"{e['id']} passes in {place}" for e in entries for place in checked_places(e)
              if not refused_in(place, e["text"])]
    history = [e for e in entries if "main-history" in e["where"]]
    if history:
        refused = main_history_refused(history)
        passed += [f"{e['id']} passes in main-history" for e in history if e["id"] not in refused]
    return passed


def lock_rows(text: str) -> list[list[str]]:
    return [line.split(" ") for line in text.splitlines() if line.strip() and not line.startswith("#")]


def row_of(kind: str, e: dict) -> list[str]:
    return [e["id"], kind, "true" if e["exact"] else "false", ",".join(e["where"]),
            hashlib.sha256(e["text"].encode("utf-8")).hexdigest()]


def main_history_refused(entries: list[dict], checker=cc) -> set[str]:
    """Commit each entry's text as a message on a scratch main, committed by GitHub's web committer
    and treated as verified, and return the ids main-history refuses."""
    tmp = Path(tempfile.mkdtemp())
    try:
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME=checker.AUTHOR[0], GIT_AUTHOR_EMAIL=checker.AUTHOR[1],
                   GIT_COMMITTER_NAME=checker.WEB_COMMITTER[0], GIT_COMMITTER_EMAIL=checker.WEB_COMMITTER[1])

        def git(*a):
            return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", *a],
                                  cwd=tmp, env=env, capture_output=True, check=True)
        git("init", "-q", "-b", "main")
        (tmp / "base").write_text("base\n")
        git("add", "base")
        git("commit", "-q", "-m", "Base")
        base = git("rev-parse", "HEAD").stdout.decode().strip()
        shas = {}
        for i, e in enumerate(entries):
            (tmp / f"f{i}").write_text(f"{i}\n")
            git("add", f"f{i}")
            git("commit", "-q", "--cleanup=verbatim", "-m", e["text"])
            shas[e["id"]] = git("rev-parse", "HEAD").stdout.decode().strip()
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        with redirect_stdout(io.StringIO()):
            found = checker.check_range(f"{base}..HEAD", branch="main", cwd=tmp, main_history=True,
                                        verify=lambda sha: True)
        return {i for i, sha in shas.items() if any(f"commit {sha[:7]}" in f for f in found)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class CorpusShapeTests(unittest.TestCase):
    maxDiff = None

    def test_entries_are_well_formed(self):
        every = [e["id"] for path in ALL.values() for e in load(path)]
        self.assertEqual(len(every), len(set(every)), "an id appears in more than one file")
        for name, path in ALL.items():
            entries = load(path)
            if name != "pending":  # pending may be empty once every ruling is made
                self.assertGreater(len(entries), 0, name)
            ids = [e["id"] for e in entries]
            self.assertEqual(len(ids), len(set(ids)), f"{name}: duplicate ids")
            for e in entries:
                self.assertEqual(set(e) - {"text"}, {"id", "source", "exact", "where", "b64"}, e["id"])
                self.assertIn(e["source"], SOURCES, e["id"])
                self.assertIsInstance(e["exact"], bool, e["id"])
                self.assertTrue(e["where"] and set(e["where"]) <= PLACES, e["id"])

    def test_corpora_only_grow(self):
        now = {e["id"] for path in ALL.values() for e in load(path)}
        for name, path in ALL.items():
            rel = path.relative_to(ROOT).as_posix()
            proc = subprocess.run(["git", "show", f"origin/main:{rel}"], cwd=ROOT, capture_output=True)
            if proc.returncode != 0:
                continue  # not on main yet
            before = {e["id"] for e in load(proc.stdout.decode("utf-8"))}
            self.assertEqual(before - now, set(), f"{name}: ids removed since main")

    def test_lock_pins_every_id_and_text(self):
        rows = lock_rows(LOCK.read_text(encoding="utf-8"))
        self.assertTrue(all(len(r) == 5 for r in rows), "lock lines are: id kind exact where sha256")
        locked = {r[0]: r for r in rows}
        self.assertEqual(len(locked), len(rows), "an id is locked twice")
        current = {e["id"]: row_of(kind, e) for kind, path in ALL.items() for e in load(path)}
        problems = []
        for i, row in locked.items():
            if i not in current:
                problems.append(f"{i}: locked but missing")
            elif current[i] != row:
                problems.append(f"{i}: locked {' '.join(row[1:4])}, now {' '.join(current[i][1:4])}"
                                + (" (text changed)" if current[i][4] != row[4] else ""))
        problems += [f"{i}: not in the lock" for i in current if i not in locked]
        self.assertEqual(problems, [], "corpus.lock")

    def test_lock_keeps_every_row_on_main(self):
        # Fails closed: only a main that exists and has no lock file skips. A missing origin/main or
        # any other git error fails. The comparison protects main only once the main ruleset
        # requires the checks status check (F1).
        rel = LOCK.relative_to(ROOT).as_posix()
        main = subprocess.run(["git", "rev-parse", "--verify", "origin/main^{commit}"], cwd=ROOT, capture_output=True)
        self.assertEqual(main.returncode, 0, "origin/main is not available; fetch it")
        listed = subprocess.run(["git", "ls-tree", "--name-only", "origin/main", rel], cwd=ROOT, capture_output=True)
        self.assertEqual(listed.returncode, 0, listed.stderr.decode("utf-8", "replace"))
        if not listed.stdout.strip():
            self.skipTest("main has no corpus.lock yet")
        proc = subprocess.run(["git", "show", f"origin/main:{rel}"], cwd=ROOT, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        now = {r[0]: r for r in lock_rows(LOCK.read_text(encoding="utf-8"))}
        problems = []
        for row in lock_rows(proc.stdout.decode("utf-8")):
            mine = now.get(row[0])
            if mine == row:
                continue
            if mine and row[1] == "pending" and mine[:1] + mine[2:] == row[:1] + row[2:]:
                continue  # a ruling moved a pending entry
            problems.append(f"{row[0]}: changed or removed since main")
        self.assertEqual(problems, [], "corpus.lock rows on main")

    def test_no_text_is_both_pending_and_decided(self):
        texts = {kind: {e["text"]: e["id"] for e in load(path)} for kind, path in ALL.items()}
        clashes = [f"{texts['pending'][t]} (pending) = {texts[k][t]} ({k})"
                   for k in ("refused", "allowed") for t in texts["pending"] if t in texts[k]]
        clashes += [f"{texts['refused'][t]} (refused) = {texts['allowed'][t]} (allowed)"
                    for t in texts["refused"] if t in texts["allowed"]]
        self.assertEqual(clashes, [], "the same text in two kinds")

    def test_no_real_session_id(self):
        # Only a made-up id of one repeated character may appear, never a real one.
        found = []
        for path in ALL.values():
            for e in load(path):
                for m in re.finditer(r"session_01([A-Za-z0-9]{20,})", e["text"]):
                    if len(set(m.group(1))) != 1:
                        found.append(e["id"])
        self.assertEqual(found, [], "corpus entries carrying a session id that may be real")


class RefusedCorpusTests(unittest.TestCase):
    maxDiff = None

    def test_every_refused_entry_is_refused_in_every_listed_place(self):
        passed = []
        for e in load(CORPORA["refused"]):
            for place in checked_places(e):
                if not refused_in(place, e["text"]):
                    passed.append(f"{e['id']} passed in {place}")
        self.assertEqual(passed, [], "refused corpus entries that got through")

    def test_every_main_history_entry_is_refused_in_main_history(self):
        entries = [e for e in load(CORPORA["refused"]) if "main-history" in e["where"]]
        missed = {e["id"] for e in entries} - main_history_refused(entries)
        self.assertEqual(missed, set(), "refused corpus entries that main-history let through")


class AllowedCorpusTests(unittest.TestCase):
    maxDiff = None

    def test_every_allowed_entry_passes_in_every_listed_place(self):
        refused = []
        entries = load(CORPORA["allowed"])
        for e in entries:
            for place in checked_places(e):
                if refused_in(place, e["text"]):
                    refused.append(f"{e['id']} refused in {place}")
        history = [e for e in entries if "main-history" in e["where"]]
        refused += [f"{i} refused in main-history" for i in sorted(main_history_refused(history))]
        self.assertEqual(refused, [], "allowed corpus entries that were refused")

    def test_every_allowed_entry_passes_the_baseline(self):
        # An allowed entry the 512e818 checker refuses would let a narrowing through by name; such
        # a text belongs in pending_ruling.jsonl for a ruling (audit 6, N5).
        spec = importlib.util.spec_from_file_location("checker_baseline", FIXTURES / "checker_baseline.py")
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        refused = []
        entries = load(CORPORA["allowed"])
        for e in entries:
            for place in checked_places(e):
                if refused_in(place, e["text"], baseline):
                    refused.append(f"{e['id']} refused by the baseline in {place}")
        history = [e for e in entries if "main-history" in e["where"]]
        refused += [f"{i} refused by the baseline in main-history"
                    for i in sorted(main_history_refused(history, baseline))]
        self.assertEqual(refused, [], "allowed entries the baseline refuses belong in pending_ruling.jsonl")


class PendingRulingTests(unittest.TestCase):
    def test_every_pending_entry_is_refused_until_ruled(self):
        # Pending fails closed: until Matt rules, every pending text is refused wherever it is
        # listed, so adding a text to pending can never let it through (audit 7, MED 4).
        self.assertEqual(pending_passes(load(PENDING)), [], "pending entries the current checker lets through")

    def test_a_pending_text_the_checker_passes_is_caught(self):
        # Even with the pending file empty, a new pending line whose text the checker passes must
        # turn this test red (audit 7, addition b).
        planted = {"id": "planted", "source": "repo", "exact": True, "where": ["message", "body", "file"],
                   "text": "Add a plain change"}
        self.assertEqual(pending_passes([planted]),
                         ["planted passes in message", "planted passes in body", "planted passes in file",
                          "planted passes in title"])

    def test_print_current_verdicts(self):
        lines = []
        for e in load(PENDING):
            places = [p for p in e["where"] if p != "main-history"]
            verdict = " ".join(f"{p}={'refused' if refused_in(p, e['text']) else 'passes'}" for p in places)
            lines.append(f"  pending {e['id']}: {verdict}")
        sys.stdout.write("\npending ruling (main-history uses the message rules):\n"
                         + ("\n".join(lines) if lines else "  none") + "\n")


class RuleTimingTests(unittest.TestCase):
    """No rule may backtrack badly: every rule, and the full message and file checks, finish within a
    second on 100,000-character lines built to defeat regular expressions (audit 4, R4)."""

    N = 100_000
    LINES = {
        "a then colons": "a" + ":" * N, "colon space": ": " * (N // 2), "letters": "a" * N,
        "underscores": "_" * N, "hyphens": "-" * N, "spaces": " " * N, "digits": "1" * N, "dots": "." * N,
        "phrase words": "gener" + "ated " * 1 + ("gener" + "ated ") * (N // 10),
        "phrase with": ("gener" + "ated with ") * (N // 15), "name words": ("cla" + "ude ") * (N // 7),
        "co dashes": "co-" * (N // 3), "angle brackets": "<" * N, "angle letters": "<a" * (N // 2),
        "label colons": "note:" * (N // 5), "apostrophes": "a'" * (N // 2),
        "key then at signs": "co-" + "authored-by " + "@" * N, "key then open angle": "co-" + "authored-by <" + "a" * N,
        "letters then host": "a" * N + ".cla" + "ude.ai",
        "newlines": "\n" * N, "newline symbols": "-\n" * (N // 2), "newline labels": "a:\n" * (N // 3),
        "capital phrase": ("Gener" + "ated with ") * (N // 15), "capital phrase lines": ("Gener" + "ated by\n") * (N // 13),
        "tagged phrase": ("<b>x gener" + "ated with ") * (N // 20),
        "tagged phrase and name": ("<b>x gener" + "ated with cla" + "ude. ") * (N // 33),
        # Audit 9, HIGH 1: runs that overlapped a gap separator and a gap word.
        "verb then apostrophes": "Built" + "'" * N, "pronoun then apostrophes": "I" + "'" * N,
        "name then apostrophes": "Cla" + "ude" + "'" * N + " wrote",
        "using then quotes": "using cla" + "ude" + " '" * (N // 2),
        "quote comma list": "api = [" + "'', " * (N // 4) + "]",
        "version dots": "cla" + "ude " + "1." * (N // 2), "escape runs": "\x1b[" * (N // 2),
        "percent layers": "%25" * (N // 3),
        "name then in-word dots": "Cla" + "ude" + ".a" * (N // 2) + " wrote",
    }

    def test_every_rule_on_long_lines(self):
        import time
        rules = [(label, p, False) for label, p in cc.MESSAGE_RULES + cc.FILE_RULES]
        rules += [(label, p, True) for label, p in cc.CASED_FOOTER_RULES] + [("tag", cc._TAG, False)]
        slow = []
        for name, line in self.LINES.items():
            for label, pattern, cased in rules:
                text = line.capitalize() if cased else line
                start = time.perf_counter()
                pattern.search(text)
                if time.perf_counter() - start > 1.0:
                    slow.append(f"{label} ({pattern.pattern[:40]}) on {name}")
            start = time.perf_counter()
            with redirect_stdout(io.StringIO()):
                cc.text_findings(line, "m")
                cc.content_findings("long.md", line.encode("utf-8"))
            if time.perf_counter() - start > 1.0:
                slow.append(f"full checks on {name}")
        self.assertEqual(slow, [], "rules slower than one second on a 100,000-character line")

if __name__ == "__main__":
    unittest.main()
