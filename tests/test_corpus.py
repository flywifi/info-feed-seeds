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
SOURCES = {"audit1", "audit2", "audit3", "audit4", "repo"}


def load(path_or_text) -> list[dict]:
    text = path_or_text.read_text(encoding="utf-8") if isinstance(path_or_text, Path) else path_or_text
    out = []
    for line in text.splitlines():
        if line.strip():
            entry = json.loads(line)
            entry["text"] = base64.b64decode(entry["b64"]).decode("utf-8")
            out.append(entry)
    return out


def refused_in(place: str, text: str) -> bool:
    if place == "message":
        return bool(cc.text_findings(text, "m"))
    if place == "body":
        return bool(cc.check_pr_text("Title", text, "pr"))
    if place == "file":
        return bool(cc.content_findings("corpus.md", (text + "\n").encode("utf-8")))
    raise ValueError(place)


def main_history_refused(entries: list[dict]) -> set[str]:
    """Commit each entry's text as a message on a scratch main, committed by GitHub's web committer
    and treated as verified, and return the ids main-history refuses."""
    tmp = Path(tempfile.mkdtemp())
    try:
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME=cc.AUTHOR[0], GIT_AUTHOR_EMAIL=cc.AUTHOR[1],
                   GIT_COMMITTER_NAME=cc.WEB_COMMITTER[0], GIT_COMMITTER_EMAIL=cc.WEB_COMMITTER[1])

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
            found = cc.check_range(f"{base}..HEAD", branch="main", cwd=tmp, main_history=True,
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
        rows = [line.split() for line in LOCK.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]
        self.assertTrue(all(len(r) == 3 for r in rows), "lock lines are: id kind sha256")
        locked = {r[0]: (r[2], r[1]) for r in rows}
        self.assertEqual(len(locked), len(rows), "an id is locked twice")
        current = {e["id"]: (hashlib.sha256(e["text"].encode("utf-8")).hexdigest(), kind)
                   for kind, path in ALL.items() for e in load(path)}
        problems = []
        for i, (sha, kind) in locked.items():
            if i not in current:
                problems.append(f"{i}: locked but missing")
            elif current[i][0] != sha:
                problems.append(f"{i}: text changed")
            elif current[i][1] != kind and kind != "pending":
                problems.append(f"{i}: moved from {kind} to {current[i][1]}")
        problems += [f"{i}: not in the lock" for i in current if i not in locked]
        self.assertEqual(problems, [], "corpus.lock")

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
            for place in e["where"]:
                if place != "main-history" and not refused_in(place, e["text"]):
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
            for place in e["where"]:
                if place != "main-history" and refused_in(place, e["text"]):
                    refused.append(f"{e['id']} refused in {place}")
        history = [e for e in entries if "main-history" in e["where"]]
        refused += [f"{i} refused in main-history" for i in sorted(main_history_refused(history))]
        self.assertEqual(refused, [], "allowed corpus entries that were refused")


class PendingRulingTests(unittest.TestCase):
    def test_print_current_verdicts(self):
        lines = []
        for e in load(PENDING):
            places = [p for p in e["where"] if p != "main-history"]
            verdict = " ".join(f"{p}={'refused' if refused_in(p, e['text']) else 'passes'}" for p in places)
            lines.append(f"  pending {e['id']}: {verdict}")
        sys.stdout.write("\npending ruling (main-history uses the message rules):\n" + "\n".join(lines) + "\n")
        self.assertTrue(lines)


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
