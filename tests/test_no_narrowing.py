"""No narrowing: the current checker refuses everything the baseline checker refused.

tests/fixtures/checker_baseline.py is a byte-for-byte copy of tools/commit_check.py at 512e818. This
test builds more than 20,000 variants with a fixed seed, from the refused corpus and from the shapes
the audits used: separator runs of 0 to 40 characters between every token, line prefixes of 0 to
40 characters (labels, punctuation, quotes, quote markers, list markers, HTML tags), 0 to 30
leading spaces, text after the name and case flips. Each variant is checked as a commit message,
as a pull request body and as a file. A variant the baseline refuses and the current checker lets
through fails the test, unless its exact text is in tests/fixtures/allowed_corpus.jsonl, where a
reviewer sees it by name.

The banned words are assembled from pieces so this file never contains them."""
import base64
import importlib.util
import json
import random
import sys
import unittest
from pathlib import Path

from tests.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures"
SEED = 20261021
MINIMUM = 20_000

CO, AUTH, BY = "Co", "Auth" + "ored", "By"
CL, SESS, CODE = "Cla" + "ude", "Sess" + "ion", "Code"
GEN = "Gener" + "ated"
SEP_CHARS = " -_.*~:;!,"
PREFIXES = ("", "Note: ", "Tooling: ", "**Note:** ", "Automatically produced notes: ", "Tool's note: ",
            "> ", "> > > ", "- ", "* ", "+ ", "1. ", "12) ", "<p>", "<sub>", "<b>", '"', "'", "(", "[",
            "...", "!! ", "## ", "| ", "    ")
SUFFIXES = ("", " today", " and reviewed by me.", ", then checked.", " for speed.", " Opus, reviewed",
            " <noreply@example.com>")
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
        out += [([GEN, prep, CL, CODE], ""), ([GEN, prep, CL], ""), ([GEN, prep, "[" + CL, CODE + "]"], "(https://x.example)")]
    out.append(([GEN, "with", "SomeTool"], ""))
    return out


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


def run(rng: random.Random, length: int) -> str:
    if rng.random() < 0.5:
        return rng.choice(SEP_CHARS) * length
    return "".join(rng.choice(SEP_CHARS) for _ in range(length))


def label(rng: random.Random, length: int) -> str:
    kind = rng.randrange(6)
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
    return rng.choice(('"', "'", "(", "[")) * max(1, length // 4)


def variants() -> list[str]:
    rng = random.Random(SEED)
    lines = corpus_lines()
    seen, out = set(), []

    def add(text):
        if text not in seen:
            seen.add(text)
            out.append(text)

    base = shapes()
    default = {CO: "-", CL: "-"}
    # Every separator run length 0 to 40 in every gap, each separator character on its own, as
    # written and in lowercase, at the start of a line and in the middle of one.
    for tokens, tail in base:
        sep = default.get(tokens[0], " ")
        for gap in range(len(tokens) - 1):
            for length in range(41):
                for ch in SEP_CHARS:
                    seps = [sep] * (len(tokens) - 1)
                    seps[gap] = ch * length
                    text = "".join(t + s for t, s in zip(tokens, seps + [""])) + tail
                    add(text)
                    add(text.lower())
                    add(MIDLINE[length % len(MIDLINE)] + text)
    # Every fixed prefix and every leading-space count, on every shape, as written and in lowercase;
    # 1 to 20 quote markers; and every sentence opening and suffix around every shape.
    for tokens, tail in base:
        sep = default.get(tokens[0], " ")
        core = sep.join(tokens) + tail
        for prefix in PREFIXES:
            for lead in range(31):
                add(" " * lead + prefix + core)
                add(" " * lead + prefix + core.lower())
        for count in range(1, 21):
            add("> " * count + core)
            add(">" * count + " " + core)
        for opening in ("",) + MIDLINE:
            for suffix in SUFFIXES:
                add(opening + core + suffix)
                add((opening + core + suffix).lower())
    # Corpus lines with prefixes, leading spaces, suffixes and case flips.
    for line in lines:
        for prefix in PREFIXES:
            add(prefix + line)
        for lead in (1, 4, 18, 20, 30):
            add(" " * lead + line)
        for suffix in SUFFIXES[1:]:
            add(line + suffix)
        for mode in (1, 2, 3):
            add(flip(line, rng, mode))
    # Random combinations until the minimum is reached.
    while len(out) < MINIMUM:
        if rng.random() < 0.3:
            core = rng.choice(lines)
        else:
            tokens, tail = rng.choice(base)
            core = "".join(t + run(rng, rng.randrange(41)) for t in tokens[:-1]) + tokens[-1] + tail
        text = " " * rng.randrange(31) + label(rng, rng.randrange(41)) + core + rng.choice(SUFFIXES)
        add(flip(text, rng, rng.randrange(4)))
    return out



def refused(module, place: str, text: str) -> bool:
    if place == "message":
        return bool(module.text_findings(text, "m"))
    if place == "body":
        return bool(module.check_pr_text("Title", text, "pr"))
    return bool(module.content_findings("variant.md", (text + "\n").encode("utf-8")))


class NoNarrowingTests(unittest.TestCase):
    maxDiff = None

    def test_nothing_the_baseline_refused_passes_now(self):
        baseline = load("checker_baseline", FIXTURES / "checker_baseline.py")
        current = load("checker_current", ROOT / "tools" / "commit_check.py")
        allowed = set()
        for line in (FIXTURES / "allowed_corpus.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                allowed.add(base64.b64decode(json.loads(line)["b64"]).decode("utf-8"))
        texts = variants()
        self.assertGreaterEqual(len(texts), MINIMUM)
        narrowed = []
        for text in texts:
            if text in allowed:
                continue
            for place in ("message", "body", "file"):
                if refused(baseline, place, text) and not refused(current, place, text):
                    narrowed.append(f"{place}: {text!a}")
        sys.stdout.write(f"\nno narrowing: {len(texts)} variants x 3 places, {len(narrowed)} narrowed\n")
        self.assertEqual(narrowed, [], "refused by the baseline, passed by this checker")


if __name__ == "__main__":
    unittest.main()
