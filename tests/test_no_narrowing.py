"""No narrowing: the current checker refuses everything the baseline checker refused.

tests/fixtures/checker_baseline.py is a byte-for-byte copy of tools/commit_check.py at 512e818 (its
sha256 is pinned below). This test builds variants with fixed seeds in named dimensions:

  gap        every separator run of 0 to 40 characters, and of 41, 64, 100 and 200, in every gap
             between tokens, with each separator character (tab, no-break space and / + = # |
             among them), as written, in lowercase and in the middle of a line
  split      tokens split by a line break, CR LF, or a line break and a quote marker
  prefix     fixed prefixes with 0 to 30 leading spaces, 1 to 20 quote markers, and letters or
             digits joined to the shape by an underscore
  lookalike  every lookalike letter in both of the baseline's tables at every letter of every
             banned word
  suffix     sentence openings and text after the name
  corpus     every refused corpus line with prefixes, suffixes and case changes
  random     20,000 seeded random combinations, always added

Each variant is checked as a commit message, a pull request body and a file; a sample also goes
through check_range (files at .md, .jsonl, .py and dotfile paths, and messages), check_pr_event and
check_message_text in a scratch repository. A variant the baseline refuses and the current checker
lets through fails the test. There are no exceptions here: a text that should pass although the
baseline refuses it goes to tests/fixtures/pending_ruling.jsonl for a ruling, and only its exact
text is skipped.

The banned words are assembled from pieces so this file never contains them."""
import base64
import hashlib
import importlib.util
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures"
BASELINE = FIXTURES / "checker_baseline.py"
BASELINE_SHA256 = "fd224233c718939f7f4ce29cd1b758efeb49dc1550901e3339f42bdd782ad05a"
SEED = 20261021
RANDOM_COUNT = 20_000
MINIMUM = {"gap": 30_000, "split": 400, "prefix": 20_000, "lookalike": 1_000, "suffix": 1_000,
           "corpus": 3_000, "random": RANDOM_COUNT}
PLACES = ("message", "body", "file")

CO, AUTH, BY = "Co", "Auth" + "ored", "By"
CL, SESS, CODE = "Cla" + "ude", "Sess" + "ion", "Code"
GEN = "Gener" + "ated"
SEP_CHARS = " -_.*~:;!,\t/+=#|\u00a0"
GAP_LENGTHS = tuple(range(41)) + (41, 64, 100, 200)
SPLITS = ("\n", "\r\n", "\n> ", " \n", "\n  ")
PREFIXES = ("", "Note: ", "Tooling: ", "**Note:** ", "Automatically produced notes: ", "Tool's note: ",
            "> ", "> > > ", "- ", "* ", "+ ", "1. ", "12) ", "<p>", "<sub>", "<b>", '"', "'", "(", "[",
            "...", "!! ", "## ", "| ", "    ", "\t")
JOINED = ("x_", "this_was_", "readme_", "body_", "a1_", "9_")
SUFFIXES = ("", " today", " and reviewed by me.", ", then checked.", " for speed.", " Opus, reviewed",
            " <noreply@example.com>", ".md", " model.")
# Sentence openings that put the shape in the middle of a line.
MIDLINE = ("x ", "Body ", "This change was ", "Note that this file was ", "The digest is ")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shapes() -> list[tuple[list[str], str]]:
    """Token lists and the tail that follows the last token."""
    out = [([CO, AUTH, BY], ": " + CL + " <noreply@anthropic.com>"), ([CO, AUTH, BY], ": X"),
           ([CO, AUTH, BY], "= X"), ([CO, AUTH, BY], " " + CL + " <x@y.z>"),
           ([CL, SESS], ": x"), ([CL, SESS], "= abc")]
    for prep in ("with", "by", "using"):
        out += [([GEN, prep, CL, CODE], ""), ([GEN, prep, CL], ""),
                ([GEN, prep, "[" + CL, CODE + "]"], "(https://x.example)")]
    out.append(([GEN, "with", "SomeTool"], ""))
    return out


def join(tokens: list[str], seps: list[str], tail: str) -> str:
    return "".join(t + s for t, s in zip(tokens, seps + [""])) + tail


def default_sep(tokens: list[str]) -> str:
    return "-" if tokens[0] in (CO, CL) else " "


def corpus_lines() -> list[str]:
    out = []
    for line in (FIXTURES / "refused_corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            text = base64.b64decode(json.loads(line)["b64"]).decode("utf-8")
            last = [x for x in text.split("\n") if x.strip()]
            if last:
                out.append(last[-1])
    return out


def flip(text: str, rng: random.Random, mode: int) -> str:
    if mode == 1:
        return text.upper()
    if mode == 2:
        return text.lower()
    if mode == 3:
        return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in text)
    return text


def label(rng: random.Random, length: int) -> str:
    kind = rng.randrange(7)
    if kind == 0:
        return "".join(rng.choice("abcdefghij klmnop") for _ in range(length)).lstrip() + ": "
    if kind == 1:
        return "".join(rng.choice("!?.,;*#~|") for _ in range(length))
    if kind == 2:
        return "> " * (length // 2)
    if kind == 3:
        return rng.choice(("- ", "* ", "+ ", "1. ", "9) ")) * max(1, length // 3)
    if kind == 4:
        return rng.choice(("<p>", "<sub>", "<b>", '<p align="center">')) * max(1, length // 8)
    if kind == 5:
        return rng.choice(JOINED)
    return rng.choice(('"', "'", "(", "[")) * max(1, length // 4)


def variants(baseline) -> dict[str, list[str]]:
    """Variants by dimension; a text appears once, in the first dimension that made it."""
    rng = random.Random(SEED)
    lines = corpus_lines()
    seen = set()
    dims = {name: [] for name in MINIMUM}

    def add(dim, text):
        if text not in seen:
            seen.add(text)
            dims[dim].append(text)

    base = shapes()
    for tokens, tail in base:
        sep = default_sep(tokens)
        gaps = len(tokens) - 1
        for gap in range(gaps):
            for length in GAP_LENGTHS:
                for ch in SEP_CHARS:
                    seps = [sep] * gaps
                    seps[gap] = ch * length
                    text = join(tokens, seps, tail)
                    add("gap", text)
                    add("gap", text.lower())
                    add("gap", MIDLINE[length % len(MIDLINE)] + text)
            for split in SPLITS:
                seps = [sep] * gaps
                seps[gap] = split
                text = join(tokens, seps, tail)
                for opening in ("",) + MIDLINE[:2]:
                    add("split", opening + text)
                    add("split", (opening + text).lower())
        core = join(tokens, [sep] * gaps, tail)
        for prefix in PREFIXES:
            for lead in range(31):
                add("prefix", " " * lead + prefix + core)
                add("prefix", " " * lead + prefix + core.lower())
        for count in range(1, 21):
            add("prefix", "> " * count + core)
            add("prefix", ">" * count + " " + core)
        for joined in JOINED:
            snake = join(tokens, ["_"] * gaps, tail)
            for text in (joined + core, joined + snake, joined + snake.lower()):
                for suffix in ("", " today", ".md"):
                    add("prefix", text + suffix)
                    add("prefix", "Add notes\n\n" + text + suffix)
        for opening in ("",) + MIDLINE:
            for suffix in SUFFIXES:
                add("suffix", opening + core + suffix)
                add("suffix", (opening + core + suffix).lower())
        # Every lookalike the baseline folds, at every letter of every banned word.
        tables = list(baseline._CAPITAL_CONFUSABLES.items()) + list(baseline._CONFUSABLES.items())
        offset = 0
        for t_index, token in enumerate(tokens):
            for i, ch in enumerate(token):
                for code, letter in tables:
                    if ch.isalpha() and letter.lower() == ch.lower():
                        position = offset + i
                        text = core[:position] + chr(code) + core[position + 1:]
                        add("lookalike", text)
                        add("lookalike", MIDLINE[2] + text)
            offset += len(token) + (len(sep) if t_index < gaps else 0)
    for line in lines:
        for prefix in PREFIXES:
            add("corpus", prefix + line)
        for lead in (1, 4, 18, 20, 30):
            add("corpus", " " * lead + line)
        for suffix in SUFFIXES[1:]:
            add("corpus", line + suffix)
        for mode in (1, 2, 3):
            add("corpus", flip(line, rng, mode))
    # A fixed random set, always added, whatever the deterministic dimensions produced.
    rng = random.Random(SEED + 1)
    while len(dims["random"]) < RANDOM_COUNT:
        if rng.random() < 0.3:
            core = rng.choice(lines)
        else:
            tokens, tail = rng.choice(base)
            seps = []
            for _ in tokens[:-1]:
                if rng.random() < 0.1:
                    seps.append(rng.choice(SPLITS))
                else:
                    length = rng.choice(GAP_LENGTHS)
                    seps.append("".join(rng.choice(SEP_CHARS) for _ in range(length)))
            core = join(tokens, seps, tail)
        text = " " * rng.randrange(31) + label(rng, rng.randrange(41)) + core + rng.choice(SUFFIXES)
        add("random", flip(text, rng, rng.randrange(4)))
    return dims


def refused(module, place: str, text: str) -> bool:
    if place == "message":
        return bool(module.text_findings(text, "m"))
    if place == "body":
        return bool(module.check_pr_text("Title", text, "pr"))
    return bool(module.content_findings("variant.md", (text + "\n").encode("utf-8")))


def pending_texts() -> set[str]:
    out = set()
    for line in (FIXTURES / "pending_ruling.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.add(base64.b64decode(json.loads(line)["b64"]).decode("utf-8"))
    return out


class NoNarrowingTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.baseline = load("checker_baseline", BASELINE)
        cls.current = load("checker_current", ROOT / "tools" / "commit_check.py")
        cls.dims = variants(cls.baseline)
        cls.pending = pending_texts()

    def test_baseline_is_pinned(self):
        self.assertEqual(hashlib.sha256(BASELINE.read_bytes()).hexdigest(), BASELINE_SHA256,
                         "the baseline changes only in a commit of its own, with this pin")

    def test_every_dimension_is_populated(self):
        counts = {dim: len(texts) for dim, texts in self.dims.items()}
        sys.stdout.write("\nno narrowing dimensions: " + ", ".join(f"{d} {n}" for d, n in counts.items()) + "\n")
        self.assertEqual({d: n for d, n in counts.items() if n < MINIMUM[d]}, {}, "dimensions below their minimum")

    def test_nothing_the_baseline_refused_passes_now(self):
        narrowed, total = [], 0
        for dim, texts in self.dims.items():
            for text in texts:
                total += 1
                if text in self.pending:
                    continue
                for place in PLACES:
                    if refused(self.baseline, place, text) and not refused(self.current, place, text):
                        narrowed.append(f"{dim} {place}: {text!a}")
        distinct = len({n.split(": ", 1)[1] for n in narrowed})
        sys.stdout.write(f"\nno narrowing: {total} variants x 3 places, {len(narrowed)} narrowed"
                         f" ({distinct} distinct texts)\n")
        self.assertEqual(narrowed, [], "refused by the baseline, passed by this checker")

    def test_sample_through_range_event_and_message(self):
        rng = random.Random(SEED + 2)
        pool = [t for texts in self.dims.values() for t in texts if t not in self.pending]
        sample = rng.sample(pool, 240)
        paths = ("notes/v{}.md", "data/v{}.jsonl", "pkg/v{}.py", ".v{}rc")
        tmp = Path(tempfile.mkdtemp())
        narrowed = []
        try:
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                       GIT_AUTHOR_NAME=self.current.AUTHOR[0], GIT_AUTHOR_EMAIL=self.current.AUTHOR[1],
                       GIT_COMMITTER_NAME=self.current.AUTHOR[0], GIT_COMMITTER_EMAIL=self.current.AUTHOR[1])

            def git(*a):
                return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", *a],
                                      cwd=tmp, env=env, capture_output=True, check=True)
            git("init", "-q", "-b", "job")
            (tmp / "base").write_text("base\n")
            git("add", "base")
            git("commit", "-q", "-m", "Base")
            base = git("rev-parse", "HEAD").stdout.decode().strip()
            marks = {}
            for i, text in enumerate(sample):
                path = paths[i % len(paths)].format(i)
                (tmp / path).parent.mkdir(parents=True, exist_ok=True)
                (tmp / path).write_bytes((text + "\n").encode("utf-8"))
                git("add", path)
                git("commit", "-q", "--cleanup=verbatim", "-m", f"Add v{i}")
                marks[f"file {path}"] = (path, text)
                (tmp / f"m{i}").write_text(f"{i}\n")
                git("add", f"m{i}")
                git("commit", "-q", "--cleanup=verbatim", "-m", text if text.strip() else "x")
                sha = git("rev-parse", "HEAD").stdout.decode().strip()
                marks[f"message {sha[:7]}"] = (f"commit {sha[:7]}", text)
            git("update-ref", "refs/remotes/origin/main", base)
            results = {}
            for name, module in (("baseline", self.baseline), ("current", self.current)):
                with redirect_stdout(io.StringIO()):
                    results[name] = "\n".join(module.check_range(f"{base}..HEAD", branch="job", cwd=tmp))
            for label_, (needle, text) in marks.items():
                if needle in results["baseline"] and needle not in results["current"]:
                    narrowed.append(f"check_range {label_}: {text!a}")
            for i, text in enumerate(sample):
                event = tmp / f"event{i}.json"
                event.write_text(json.dumps({"pull_request": {"number": 7, "title": "T", "body": text}}))
                if self.baseline.check_pr_event(str(event)) and not self.current.check_pr_event(str(event)):
                    narrowed.append(f"check_pr_event: {text!a}")
                if self.baseline.check_message_text(text) and not self.current.check_message_text(text):
                    narrowed.append(f"check_message_text: {text!a}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        sys.stdout.write(f"\nno narrowing sample: {len(sample)} variants through check_range (files and"
                         f" messages), check_pr_event and check_message_text, {len(narrowed)} narrowed\n")
        self.assertEqual(narrowed, [], "refused by the baseline, passed by this checker")


if __name__ == "__main__":
    unittest.main()
