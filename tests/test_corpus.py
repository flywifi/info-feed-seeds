"""The refused and allowed corpora: what every rule change must keep true.

tests/fixtures/refused_corpus.jsonl holds every string an audit proved must be refused, and
tests/fixtures/allowed_corpus.jsonl holds text that must pass (prose about the rules, real commit
messages and pull request bodies). Each line is {"id", "source", "exact", "where", "b64"}; the text
is base64 so no scanner ever sees the literals. "exact" says whether the audit gave the string byte
for byte or it was reconstructed from the audit's description.

A rule change is acceptable only when both corpora pass. The corpora only grow: an id that exists
on origin/main must still exist here."""
import base64
import io
import json
import os
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


class CorpusShapeTests(unittest.TestCase):
    def test_entries_are_well_formed(self):
        for name, path in CORPORA.items():
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
        for name, path in CORPORA.items():
            rel = path.relative_to(ROOT).as_posix()
            proc = subprocess.run(["git", "show", f"origin/main:{rel}"], cwd=ROOT, capture_output=True)
            if proc.returncode != 0:
                continue  # not on main yet
            before = {e["id"] for e in load(proc.stdout.decode("utf-8"))}
            now = {e["id"] for e in load(path)}
            self.assertEqual(before - now, set(), f"{name}: ids removed since main")


class RefusedCorpusTests(unittest.TestCase):
    def test_every_refused_entry_is_refused_in_every_listed_place(self):
        passed = []
        for e in load(CORPORA["refused"]):
            for place in e["where"]:
                if place != "main-history" and not refused_in(place, e["text"]):
                    passed.append(f"{e['id']} passed in {place}")
        self.assertEqual(passed, [], "refused corpus entries that got through")

    def test_every_main_history_entry_is_refused_in_main_history(self):
        entries = [e for e in load(CORPORA["refused"]) if "main-history" in e["where"]]
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
            missed = [i for i, sha in shas.items() if not any(f"commit {sha[:7]}" in f for f in found)]
            self.assertEqual(missed, [], "refused corpus entries that main-history let through")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AllowedCorpusTests(unittest.TestCase):
    def test_every_allowed_entry_passes_in_every_listed_place(self):
        refused = []
        for e in load(CORPORA["allowed"]):
            for place in e["where"]:
                if refused_in(place, e["text"]):
                    refused.append(f"{e['id']} refused in {place}")
        self.assertEqual(refused, [], "allowed corpus entries that were refused")



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
