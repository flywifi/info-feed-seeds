"""Golden cases for tools/commit_check.py.

tests/fixtures/golden_cases.jsonl holds planted refusals, negative controls, limit cases and decided
wording cases, base64-encoded so no scanner reads them. Each case gives the checker's verdict for
the text as a commit message, a pull request title, a pull request body, a file and a file under seeds/:
"block", "advisory" or "pass". Ids describe the shape of the text, never where a case came from.

Also here: every rule has a "Limit:" text and a case pinning it, the exemptions file holds no stale
entry, exemptions cover one exact text, advisory lines never change the exit code, every rule is
fast on 100,000-character lines, and four mutations of the checker turn the golden comparison red.

The banned words are assembled from pieces so this file never contains them."""
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import commit_check as cc  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "golden_cases.jsonl"
EXEMPTIONS = ROOT / cc.EXEMPTIONS_FILE
PLACES = ("message", "title", "body", "file", "seed")  # seed: a file under seeds/
KINDS = ("planted", "control", "limit", "decided")
VERDICTS = ("block", "advisory", "pass")
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME = "Cla" + "ude"
RULE_SECONDS = 0.25  # per rule and input on CI; under 0.07 s on a desktop


def golden_text() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def exemptions_text():
    return EXEMPTIONS.read_text(encoding="utf-8") if EXEMPTIONS.exists() else None


def tracked_paths() -> set:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return {p for p in out.decode("utf-8").split("\x00") if p}


def read_tracked(path: str) -> bytes:
    return (ROOT / path).read_bytes()


def load_cases(text: str) -> list:
    cases = []
    for line in text.splitlines():
        if line.strip():
            case = json.loads(line)
            case["text"] = base64.b64decode(case["b64"]).decode("utf-8")
            cases.append(case)
    return cases


def expected(case: dict, place: str) -> str:
    want = case["expect"]
    if isinstance(want, str):
        return want
    return want[place] if place in want else want["file"]  # a seed verdict defaults to the file verdict


def mismatches(checker, cases) -> list:
    out = []
    for case in cases:
        for place in PLACES:
            got = checker.verdict(case["text"], place)
            if got != expected(case, place):
                out.append(f"{case['id']} as {place}: expected {expected(case, place)}, got {got}")
    return out


def fired(checker, text: str, place: str) -> set:
    """Rule ids that match the text in one place ("dash" for the dash check)."""
    if place in ("file", "seed"):
        path = "seeds/golden.md" if place == "seed" else "golden.md"
        rules = tuple(checker._file_rules(path)) + checker._rules(False, "file")
    else:
        rules = tuple(r for r in checker.RULES if "text" in r.places)
    ids = {r.id for r in rules if checker._scan(text, (r,))}
    return ids | ({"dash"} if checker._dash_hits(text) else set())


def timing_inputs() -> dict:
    n = 100_000
    return {
        "author and equals signs": "auth" + "or" + "=" * n, "author and colons": "auth" + "or" + ":" * n,
        "apostrophes after a verb": "Built" + "'" * n, "apostrophes after I": "I" + "'" * n,
        "quote-comma list": "api = [" + "'', " * (n // 4) + "]", "digits then spaces": "1" * (n // 2) + "a" + " " * (n // 2),
        "deep percent and escape": ("%25" * 6 + "41") * (n // 20) + "\x1b", "dots": "a." * (n // 2),
        "spaces after the name": NAME + " " * n, "word and space": "a " * (n // 2), "pronouns": "i " * (n // 2),
        "commas": ", " * (n // 2), "phrase and words": "gene" + "rated with " + "a " * (n // 2),
        "quote markers": "> " * (n // 2) + "x", "brackets": "gene" + "rated with " + "[" * n,
        "id list": "(" + "F1, " * (n // 4), "label run": "a" * 60 + " " * n + ":", "names": (NAME + " ") * (n // 7),
        "verbs": "built " * (n // 6), "credit": "thanks to " * (n // 10), "session run": "session_" + "-" * n,
        "angle brackets": "co-auth" + "ored-by " + "<" * n, "escapes": "\x1b[" * (n // 2), "osc open": "\x1b]" + "a" * n,
        "review words": "aud" + "it " * (n // 6), "severity words": "HIGH " * (n // 5), "newlines": "\n" * n,
        "made and spaces": "made " * (n // 5), "made and newlines": "made\n" * (n // 5),
        "thanks and spaces": "thanks " * (n // 7), "name and surname": (NAME + " Shannon ") * (n // 15),
        "passive use": (NAME + " was used ") * (n // 16),
        "verbs then a name": ("done " * 38 + NAME + " ") * (n // 197), "verb and short name": "had    gpt    " * (n // 14),
        "pronoun and verb": "we     had     " * (n // 15), "split name": "chat gpt     " * (n // 13),
        "at signs": "@" * n, "address runs": "a@b." * (n // 4), "comment openers": "<!-- " * (n // 5),
        "slashes": "/" * n, "link openers": "](" * (n // 2), "hyphen keys": "A-" * (n // 2) + ":",
        "digits and plus": "1" * (n - 1) + "+", "bracket runs": "[" * n, "determiners": "the " * (n // 4),
        "bylines on one line": ("by " + NAME + " ") * (n // 10), "bulleted bylines": ("- by " + NAME + "\n") * (n // 11),
    }


class GoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases(golden_text())

    def test_case_shape(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        for c in self.cases:
            with self.subTest(case=c["id"]):
                self.assertEqual(set(c) - {"text"}, {"id", "b64", "expect", "rule", "kind", "note"})
                self.assertTrue(ID_RE.fullmatch(c["id"]))
                self.assertIn(c["kind"], KINDS)
                self.assertIn(c["rule"], cc.RULE_IDS)
                for place in PLACES:
                    self.assertIn(expected(c, place), VERDICTS)
                # Ids and notes are public text: they pass the checker themselves.
                self.assertEqual(cc.text_findings(c["id"] + "\n" + c["note"], "case"), [])

    def test_golden_verdicts(self):
        self.assertEqual(mismatches(cc, self.cases), [])

    def test_planted_cases_fire_their_own_rule(self):
        for c in self.cases:
            if c["kind"] not in ("planted", "decided") or all(expected(c, p) == "pass" for p in PLACES):
                continue
            with self.subTest(case=c["id"]):
                self.assertTrue(any(c["rule"] in fired(cc, c["text"], p) for p in PLACES), c["rule"])

    def test_limit_cases_are_not_read_by_their_rule(self):
        for c in self.cases:
            if c["kind"] != "limit":
                continue
            rule = next((r for r in cc.RULES if r.id == c["rule"]), None)
            places = [p for p in PLACES if rule is None or ("file" if p in ("file", "seed") else "text") in rule.places]
            with self.subTest(case=c["id"]):
                self.assertFalse(any(c["rule"] in fired(cc, c["text"], p) for p in places), c["rule"])

    def test_every_rule_states_and_pins_its_limit(self):
        for rule in cc.RULES:
            mine = [c for c in self.cases if c["rule"] == rule.id]
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.limit.startswith("Limit: "))
                self.assertTrue(any(c["kind"] == "limit" for c in mine), "no limit case")
                self.assertTrue(any(c["kind"] in ("planted", "decided") for c in mine), "no planted case")

    def test_negative_controls_exist(self):
        controls = [c for c in self.cases if all(expected(c, p) == "pass" for p in PLACES)]
        self.assertGreaterEqual(len(controls), 30)


class ExemptionTests(unittest.TestCase):
    def test_exemptions_file_has_no_stale_entry(self):
        entries, problems = cc.load_exemptions(exemptions_text())
        self.assertEqual(problems, [])
        tracked = tracked_paths()
        for e in entries:
            with self.subTest(path=e["path"], rule=e["rule"]):
                self.assertIn(e["path"], tracked, "the path is not tracked; drop the entry")
                text, _ = cc.file_text(read_tracked(e["path"]))
                self.assertIn((e["rule"], e["match_sha256"]), cc.file_matches(text or "", e["path"]),
                              "the entry exempts nothing; drop it")

    def test_an_exemption_covers_one_exact_text(self):
        line = "Gene" + "rated with SomeTool"
        (rule, sha), = cc.file_matches(line + "\n")
        entry = {"path": "a.md", "rule": rule, "match_sha256": sha, "reason": "a quoted example of the footer shape"}
        self.assertEqual(cc.content_findings("a.md", (line + "\n").encode(), [entry]), [])
        self.assertTrue(cc.content_findings("b.md", (line + "\n").encode(), [entry]), "another path")
        self.assertTrue(cc.content_findings("a.md", (line + "\n" + line + " 2\n").encode(), [entry]), "another text")

    def test_malformed_entries_are_reported(self):
        good = {"path": "a.md", "rule": "footer-line", "match_sha256": "0" * 64, "reason": "x" * 25}
        bad = [dict(good, reason="too short"), dict(good, rule="generated-near"), dict(good, rule="session-mention"),
               dict(good, match_sha256="xyz"), {"path": "a.md"}, good, good]
        entries, problems = cc.load_exemptions(json.dumps({"exemptions": bad}))
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(problems), 6, problems)
        self.assertTrue(cc.load_exemptions("not json")[1])
        self.assertEqual(cc.load_exemptions(None), ([], []))


class OutputTests(unittest.TestCase):
    def test_advisory_lines_never_fail(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            findings = cc.check_message_text("The digest was gene" + "rated with care.\nThe " + NAME + " step labels items.\n")
            code = cc.report(findings, "commit message")
        self.assertEqual((findings, code), ([], 0))
        self.assertIn("ADVISORY:", buf.getvalue())

    def test_advisory_lines_are_warnings_in_actions(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), redirect_stdout(buf):
            cc.check_pr_text("Fix", "The digest was gene" + "rated with care.\nThe " + NAME + " step labels items.\n", "pull request 1")
        self.assertIn("::warning title=commit_check advisory::pull request 1 body", buf.getvalue())

    def test_block_findings_fail(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cc.report(cc.check_message_text("Fix \u2014 it\n"), "commit message"), 1)


class TimingTests(unittest.TestCase):
    def test_every_rule_is_fast_on_long_lines(self):
        slow = []
        for label, text in timing_inputs().items():
            folded = {False: cc._fold(text), True: cc._fold(text, keep_case=True)}
            for rule in cc.RULES + tuple(cc.SEED_RULES.values()):
                start = time.perf_counter()
                rule.search(folded[rule.cased])
                took = time.perf_counter() - start
                if took > RULE_SECONDS:
                    slow.append(f"{rule.id} on {label}: {took:.2f} s")
        self.assertEqual(slow, [])


class MutationTests(unittest.TestCase):
    """Four edits a careless change could make. Each must turn the golden comparison red."""

    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases(golden_text())

    def test_dropping_a_block_rule(self):
        rules = tuple(r for r in cc.BLOCK_RULES if r.id != "coauthor-trailer") + cc.ADVISORY_RULES
        with mock.patch.object(cc, "RULES", rules):
            self.assertTrue(mismatches(cc, self.cases))

    def test_promoting_advisory_rules_to_block(self):
        promoted = tuple(cc.Rule(r.id, r.label, True, r.places, r.matcher, r.limit, cased=r.cased)
                         for r in cc.ADVISORY_RULES)
        with mock.patch.object(cc, "RULES", cc.BLOCK_RULES + promoted):
            self.assertTrue(mismatches(cc, self.cases))

    def test_skipping_the_dash_check(self):
        with mock.patch.object(cc, "_dash_hits", lambda text: []):
            self.assertTrue(mismatches(cc, self.cases))

    def test_widening_the_footer_line_past_its_limit(self):
        wide = cc.Rule("footer-line", "generated footer", True, cc.BOTH, cc._LEAD + cc._LABEL + cc._PHRASE,
                       "Limit: none", re.M)
        rules = tuple(wide if r.id == "footer-line" else r for r in cc.RULES)
        with mock.patch.object(cc, "RULES", rules):
            self.assertTrue(mismatches(cc, self.cases))


if __name__ == "__main__":
    unittest.main()
