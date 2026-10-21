#!/usr/bin/env python3
"""Red-then-green evidence for the PR 7 audits: every audit probe, run through older copies of
tools/commit_check.py and through the working tree's copy, side by side.

Usage: python3 tools/audit_redgreen.py [--old REF ...]     (default: --old ba76314 --old 6686d0a)

Each older checker is read with `git show REF:tools/commit_check.py`; the newest is the file next to
this script. The result column compares the working tree with the LAST --old ref:
  red->green   the last ref got it wrong and the working tree gets it right
  kept         both get it right (a regression test)
  WRONG        the working tree gets it wrong
Probes the auditor gave as exact strings are byte for byte. Where the auditor named only the code
point and not its position, the probe sweeps that character through every position of the token
and reports how many positions were refused. Banned strings are assembled from pieces at run
time, so this file never contains them. Standard library only; ASCII output.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
G, CL = "Gener" + "ated", "Cla" + "ude"
CO, SESSION = "Co-" + "Authored-By", CL + "-Session"
WEB = {"GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com"}


def load(name: str, source: str):
    tmp = Path(tempfile.mkdtemp()) / f"{name}.py"
    tmp.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# --------------------------------------------------------------------------- probes

N1_FOOTERS = {
    "sub robot (as written)": "<sub>(robot) " + G + " with [" + CL + " Code](https://" + CL.lower() + ".com/"
                              + CL.lower() + "-code)</sub>",
    "sub robot (emoji)": "<sub>\U0001F916 " + G + " with [" + CL + " Code](https://" + CL.lower() + ".com/"
                         + CL.lower() + "-code)</sub>",
    "note label": "Note: " + G + " with [...]",
    "sentence": "This PR was " + G.lower() + " with " + CL + " Code",
    "html p": '<p align="center">' + G + " with " + CL + " Code</p>",
    "bold label": "**Tooling:** " + G + " by " + CL,
    "version prefix": "v2 " + G + " using " + CL + " Code",
}
CAPITALS = {"A": ["\u0410", "\u0391"], "B": ["\u0412", "\u0392"], "C": ["\u0421"], "E": ["\u0415", "\u0395"],
            "H": ["\u041d", "\u0397"], "I": ["\u0406", "\u0399"], "O": ["\u041e", "\u039f"], "S": ["\u0405"],
            "T": ["\u0422", "\u03a4"], "Y": ["\u0423", "\u03a5"]}


def sweeps():
    """(label, [variants]) for characters named by code point but not by position."""
    out = []
    for token in (CO + ": x", SESSION + ": x"):
        variants = []
        for i, ch in enumerate(token):
            for look in CAPITALS.get(ch.upper(), []):
                variants.append(token[:i] + look + token[i + 1:])
        out.append((f"capital lookalike at every letter of '{token.split(':')[0][:9]}...'", variants))
    authored = CO + ": x"
    start = authored.index("Authored")
    for code in ("\u034f", "\ufe0f"):
        variants = [authored[:i] + code + authored[i:] for i in range(start + 1, start + len("Authored"))]
        out.append((f"U+{ord(code):04X} inside 'Authored' at every position", variants))
    for code in ("\u3164", "\u115f", "\u1160", "\uffa0"):
        variants = [authored[:i] + code + authored[i:] for i in range(1, len(CO))]
        out.append((f"Hangul filler U+{ord(code):04X} at every position", variants))
    return out


# --------------------------------------------------------------------------- scenarios

class Repo:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        ident = {"GIT_AUTHOR_NAME": "flywifi", "GIT_AUTHOR_EMAIL": "236871844+flywifi@users.noreply.github.com",
                 "GIT_COMMITTER_NAME": "flywifi", "GIT_COMMITTER_EMAIL": "236871844+flywifi@users.noreply.github.com"}
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **ident)
        self.git("init", "-q", "-b", "main")
        self.commit("r", "Add r")

    def git(self, *a, extra=None):
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", *a],
                              cwd=self.dir, env={**self.env, **(extra or {})}, check=True, capture_output=True)

    def commit(self, name, message, extra=None):
        (self.dir / name).write_text(name + "\n")
        self.git("add", name)
        self.git("commit", "-q", "-m", message, extra=extra)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.decode().strip()


def main_history(mod, repo: Repo, rng: str, verify) -> list:
    try:
        return quiet(mod.check_range, rng, branch="main", cwd=repo.dir, main_history=True, verify=verify)
    except TypeError:  # a checker from before --main-history ran the plain range check on main
        return quiet(mod.check_range, rng, branch="main", cwd=repo.dir)


def scenario_n1_main_history(mod) -> bool:
    repo = Repo()
    base = repo.head()
    for label in N1_FOOTERS:
        repo.commit(label.replace(" ", "-").replace("(", "").replace(")", ""), "Merge pull request 1\n\n" + N1_FOOTERS[label], extra=WEB)
    repo.git("update-ref", "refs/remotes/origin/main", repo.head())
    found = main_history(mod, repo, f"{base}..HEAD", verify=lambda sha: True)
    return sum("footer" in f for f in found) == len(N1_FOOTERS)


def scenario_n6_forged_merge(mod) -> bool:
    repo = Repo()
    base = repo.head()
    repo.git("checkout", "-q", "-b", "side")
    repo.commit("s", "Side work")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "side", "-m", "Merge pull request 2", extra=WEB)
    repo.git("update-ref", "refs/remotes/origin/main", repo.head())
    return bool(main_history(mod, repo, f"{base}..HEAD", verify=lambda sha: False))


def scenario_n3_known(mod) -> bool:
    """True when an acknowledged bad landing no longer fails main-history."""
    repo = Repo()
    base = repo.head()
    bad = repo.commit("m", "Merge pull request 3\n\n" + SESSION + ": abc", extra=WEB)
    (repo.dir / "tools").mkdir()
    (repo.dir / "tools" / "main_history_known.txt").write_text(bad + " acknowledged by the owner\n")
    repo.git("update-ref", "refs/remotes/origin/main", repo.head())
    return not main_history(mod, repo, f"{base}..HEAD", verify=lambda sha: True)


# --------------------------------------------------------------------------- table

def rows():
    out = []
    for label, text in N1_FOOTERS.items():
        for where, fn in (("message", lambda m, t: m.text_findings("Add a\n\n" + t, "m")),
                          ("PR body", lambda m, t: m.check_pr_text("Job", "Body\n\n" + t, "pr")),
                          ("file", lambda m, t: m.content_findings("notes.md", (t + "\n").encode()))):
            out.append(("N1", f"{label} in {where}", (lambda m, f=fn, t=text: bool(f(m, t))), True))
    out.append(("N1", "all seven footers refused in main-history", scenario_n1_main_history, True))
    out.append(("N9", "prose: Cite the paper co-" + "authored by Smith",
                lambda m: bool(m.text_findings("Cite the paper co-" + "authored by Smith", "m")), False))
    out.append(("N6", "forged GitHub merge, unverified, in main-history", scenario_n6_forged_merge, True))
    out.append(("N3", "acknowledged bad landing in main-history", lambda m: not scenario_n3_known(m), False))
    for label, variants in sweeps():
        out.append(("N4", label, (lambda m, vs=variants: sum(bool(m.text_findings(v, "m")) for v in vs)), len(variants)))
    return out


def main(argv: list[str]) -> int:
    refs = [argv[i + 1] for i, a in enumerate(argv) if a == "--old"] or ["ba76314", "6686d0a"]
    olds = []
    for ref in refs:
        src = subprocess.run(["git", "show", f"{ref}:tools/commit_check.py"], cwd=HERE, capture_output=True)
        if src.returncode != 0:
            print(f"cannot read tools/commit_check.py at {ref}")
            return 2
        olds.append((ref, load("cc_" + ref, src.stdout.decode("utf-8"))))
    new = load("cc_new", (HERE / "commit_check.py").read_text(encoding="utf-8"))
    checkers = olds + [("new", new)]

    def show(value, want):
        if isinstance(want, bool):
            return "REFUSED" if value else "passed"
        return f"{value}/{want}"

    lines = []
    for finding, label, probe, want in rows():
        values = [probe(mod) for _, mod in checkers]
        right = [v == want for v in values]
        result = "WRONG" if not right[-1] else ("kept" if right[-2] else "red->green")
        lines.append((finding, label, [show(v, want) for v in values], result))
    width = max(len(label) for _, label, _, _ in lines)
    head = " | ".join(f"{ref:8}" for ref, _ in checkers)
    print(f"{'finding':7} | {'probe':{width}} | {head} | result (vs {refs[-1]})")
    for finding, label, shown, result in lines:
        print(f"{finding:7} | {label:{width}} | " + " | ".join(f"{s:8}" for s in shown) + f" | {result}")
    counts = {r: sum(1 for *_, x in lines if x == r) for r in ("red->green", "kept", "WRONG")}
    print(f"{len(lines)} probes: {counts['red->green']} red->green, {counts['kept']} kept, {counts['WRONG']} WRONG")
    return 1 if counts["WRONG"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
