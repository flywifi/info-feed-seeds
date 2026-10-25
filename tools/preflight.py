#!/usr/bin/env python3
"""Rehearse the CI `checks` job locally, step for step, from .github/workflows/ci.yml itself.

Usage:
  python3 tools/preflight.py                 run every step of the checks job in this clone
  python3 tools/preflight.py --fast          the same, without the full unit-test step (partial)
  python3 tools/preflight.py --pre-push      as the pre-push hook: git's ref lines on stdin
  python3 tools/preflight.py --list          print the steps and how each is rehearsed
  python3 tools/preflight.py --check-parity  confirm every step of the checks job can be rehearsed
  python3 tools/preflight.py --selftest      prove the reader and the parity rules on samples

The step list is read from ci.yml on every run, so it cannot drift from CI. A `run:` step is run
with bash, in the repository root, with the job's env, RUNNER_TEMP set to a fresh temporary folder,
and `python3` resolving to this interpreter. A step without `if:` runs only while every earlier step
passed and `if: ${{ !cancelled() }}` always runs, as in GitHub Actions. Each step has a timeout.
`uses:` steps are covered by a declared route (DECLARED), each with its reason; --check-parity
fails on any other construct, on a variable the rehearsal does not set, and on a stale declaration.

Exit 0 every step passed (with --fast: every step run passed, and the summary says it was partial);
1 a step failed; 2 a usage error or a ci.yml the reader cannot read (fail closed); 3 DID NOT RUN:
the tree has uncommitted changes, HEAD is not the commit being pushed, bash is missing or a step
timed out. Under CI (CI=true) a DID NOT RUN is a failure and --fast and IFS_SKIP_PREFLIGHT are
refused. Standard library only; ASCII output.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ".github/workflows/ci.yml"
JOB = "checks"
STEP_TIMEOUT = 900
FAST_SKIP = ("Unit tests",)
# `uses:` actions the rehearsal covers by another route, each with its reason (25+ characters).
DECLARED = {
    "actions/checkout": "the rehearsal runs in this clone at HEAD, with full history, and refuses a dirty tree",
    "actions/setup-python": "the rehearsal runs on this interpreter and prints its version beside the one CI uses",
}
# Variables a run block may read: set by the job env or by the rehearsal.
# IFS_PERSON_FINGERPRINTS is a repository secret in CI; the rehearsal passes the local environment's value
# through when there is one. Without it the base-branch copy of the checker in RUNNER_TEMP reads only the
# test token, as a CI run without the secret would; the branch's own checker beside the ignored local file
# still reads that file.
KNOWN_VARS = {"BASE_REF", "PR_NUMBER", "RUNNER_TEMP", "GITHUB_WORKSPACE", "GITHUB_REF", "IFS_PERSON_FINGERPRINTS"}
STEP_KEYS = {"name", "uses", "with", "if", "run"}
ALWAYS = "${{ !cancelled() }}"


def say(message: str = "") -> None:
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")
    sys.stdout.flush()


def in_ci() -> bool:
    return os.environ.get("CI") == "true"


# --------------------------------------------------------------------------- reading ci.yml

def read_job(text: str, job: str = JOB) -> tuple[dict, list[dict]]:
    """(job env, steps) for one job of a workflow in the plain block style ci.yml uses. Raises
    ValueError on anything else, so an unreadable workflow fails closed."""
    lines = text.split("\n")
    try:
        start = lines.index(f"  {job}:")
    except ValueError:
        raise ValueError(f"job {job!r} not found") from None
    body = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith("   ") and not line.lstrip().startswith("#"):
            break
        body.append(line)
    env, steps, section, step, key = {}, [], None, None, None
    for line in body:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 4:
            section = line.strip().rstrip(":").split(":")[0]
            continue
        if section == "env" and indent == 6:
            name, _, value = line.strip().partition(":")
            env[name] = value.strip().strip('"')
        elif section == "steps":
            stripped = line.strip()
            if indent == 6 and stripped.startswith("- "):
                step, key = {}, None
                steps.append(step)
                stripped = stripped[2:]
                indent = 8
            if step is None:
                raise ValueError(f"unreadable line in steps: {line!r}")
            if indent == 8:
                name, _, value = stripped.partition(":")
                if name not in STEP_KEYS:
                    raise ValueError(f"step key {name!r} is not read by the rehearsal")
                key = name
                value = value.strip()
                step[name] = "" if value == "|" else (value if name != "with" else {})
                if name == "run" and value == "|":
                    step["block"] = True
            elif indent >= 10 and key == "run" and step.get("block"):
                step["run"] += line[10:] + "\n"
            elif indent == 10 and key == "with":
                name, _, value = stripped.partition(":")
                step["with"][name] = value.strip().strip('"')
            else:
                raise ValueError(f"unreadable line in steps: {line!r}")
    if not steps:
        raise ValueError(f"job {job!r} has no steps")
    for n, s in enumerate(steps, 1):
        s.setdefault("name", s.get("uses", f"step {n}"))
        s.pop("block", None)
    return env, steps


def parity_problems(env: dict, steps: list[dict]) -> list[str]:
    out, used = [], set()
    for name in env:
        if name not in KNOWN_VARS:
            out.append(f"job env {name}: not set by the rehearsal")
    for s in steps:
        where = f"step {s['name']!r}"
        if "if" in s and s["if"] != ALWAYS:
            out.append(f"{where}: if {s['if']!r} is not rehearsed (only {ALWAYS})")
        if "uses" in s:
            action = s["uses"].split("@")[0]
            if action not in DECLARED:
                out.append(f"{where}: {action} has no declared route")
            used.add(action)
        elif "run" in s:
            if re.search(r"\|\|\s*true\b", s["run"]):
                out.append(f"{where}: '|| true' hides a failure")
            for var in set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", s["run"])):
                if var not in KNOWN_VARS:
                    out.append(f"{where}: reads ${var}, which the rehearsal does not set")
            if "${{" in s["run"]:
                out.append(f"{where}: a workflow expression in the command")
        else:
            out.append(f"{where}: neither uses nor run")
    for action, reason in DECLARED.items():
        if len(reason) < 25:
            out.append(f"declared route {action}: the reason needs 25+ characters")
        if action not in used:
            out.append(f"declared route {action}: no step uses it; drop the entry")
    return out


# --------------------------------------------------------------------------- running

def find_bash() -> str | None:
    if os.environ.get("IFS_BASH"):
        return os.environ["IFS_BASH"]
    if os.name == "nt":
        git_exe = shutil.which("git")
        if git_exe:
            # From a shell git is <git>\cmd\git.exe; inside a hook it is <git>\<mingw64|ucrt64>\libexec\git-core.
            for root in Path(git_exe).resolve().parents[1:4]:
                for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
                    if candidate.exists():
                        return str(candidate)
        return None  # never WSL's bash.exe: it runs another system
    return shutil.which("bash")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def git_local_env_names() -> set[str]:
    """Variables that point git at one repository (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and the rest)."""
    proc = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=False)
    names = set(proc.stdout.split()) if proc.returncode == 0 else set()
    return names | {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                    "GIT_PREFIX"}


def job_env(env: dict, runner_temp: str) -> dict:
    out = dict(os.environ)
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    # CI and GITHUB_ACTIONS are set on every Actions run, so steps that read them behave here as in CI.
    out.update({"BASE_REF": os.environ.get("BASE_REF") or "main", "PR_NUMBER": "", "RUNNER_TEMP": runner_temp,
                "GITHUB_WORKSPACE": str(ROOT), "GITHUB_REF": f"refs/heads/{branch}", "CI": "true",
                "GITHUB_ACTIONS": "true"})
    for name, value in env.items():
        if "${{" not in value:
            out[name] = value
    # A hook in a linked worktree receives GIT_DIR and its relatives. Steps find the repository from
    # their working directory instead, and the scratch repositories they build cannot reach this one.
    for name in git_local_env_names():
        out.pop(name, None)
    shim = Path(runner_temp) / "bin"
    shim.mkdir()
    exe = Path(sys.executable).as_posix()
    (shim / "python3").write_text(f'#!/bin/sh\nexec "{exe}" "$@"\n', encoding="utf-8", newline="\n")
    (shim / "python3").chmod(0o755)
    out["PATH"] = str(shim) + os.pathsep + out.get("PATH", "")
    return out


def rehearse(steps: list[dict], env: dict, fast: bool, timeout: int) -> int:
    bash = find_bash()
    results, failed, did_not_run = [], False, False
    with tempfile.TemporaryDirectory() as runner_temp:
        environ = job_env(env, runner_temp)
        for s in steps:
            name = s["name"]
            if "uses" in s:
                version = s.get("with", {}).get("python-version")
                note = f" (CI: Python {version}; here: {sys.version.split()[0]})" if version else ""
                results.append(("COVERED", name, DECLARED.get(s["uses"].split("@")[0], "?") + note, 0.0))
                continue
            if failed and s.get("if") != ALWAYS:
                results.append(("SKIPPED", name, "an earlier step failed, as in CI", 0.0))
                continue
            if fast and name in FAST_SKIP:
                results.append(("SKIPPED", name, "fast mode", 0.0))
                continue
            if bash is None:
                results.append(("DID NOT RUN", name, "no bash found (Git Bash on Windows)", 0.0))
                did_not_run = True
                continue
            say(f"preflight: running {name}")
            start = time.perf_counter()
            try:
                proc = subprocess.run([bash, "-e", "-c", s["run"]], cwd=ROOT, env=environ, timeout=timeout,
                                      capture_output=True)
            except subprocess.TimeoutExpired:
                results.append(("DID NOT RUN", name, f"timed out after {timeout} s", time.perf_counter() - start))
                did_not_run = True
                continue
            took = time.perf_counter() - start
            if proc.returncode == 0:
                results.append(("PASS", name, "", took))
            else:
                failed = True
                tail = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip().splitlines()[-12:]
                results.append(("FAIL", name, f"exit {proc.returncode}\n      " + "\n      ".join(tail), took))
    for status, name, detail, took in results:
        say(f"preflight: {status:11s} {name} ({took:.1f} s){': ' + detail if detail else ''}")
    if failed:
        say("preflight: FAILED; CI would fail the checks job")
        return 1
    if did_not_run:
        if in_ci():
            say("preflight: a step did not run; under CI that is a failure")
            return 1
        say("preflight: DID NOT RUN in full; this is not a clean rehearsal")
        return 3
    if fast:
        say(f"preflight: passed in fast mode; skipped {', '.join(FAST_SKIP)}: a partial rehearsal, not a clean one")
    else:
        say("preflight: every step of the checks job passed")
    return 0


def ready(pushed: list[str]) -> str | None:
    """Why the rehearsal cannot stand for what CI will see, or None."""
    if git("status", "--porcelain", "--untracked-files=normal"):
        return "the working tree has uncommitted or untracked changes; CI sees only commits"
    head = git("rev-parse", "HEAD")
    for sha in pushed:
        if sha != head:
            return f"the push sends {sha[:7]}, but HEAD is {head[:7]}; check out what you push"
    return None


def main(argv: list[str]) -> int:
    args = set(argv)
    if not args <= {"--fast", "--pre-push", "--list", "--check-parity", "--selftest"} or "-h" in argv:
        say(__doc__.strip())
        return 2
    if "--selftest" in args:
        return selftest()
    if in_ci() and ("--fast" in args or os.environ.get("IFS_SKIP_PREFLIGHT") == "1"):
        say("preflight: --fast and IFS_SKIP_PREFLIGHT are refused under CI")
        return 2
    pushed = []
    if "--pre-push" in args:
        for line in sys.stdin.read().splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[1] != "0" * 40:
                pushed.append(parts[1])
        if not pushed:
            say("preflight: nothing but deletions to push; no rehearsal needed")
            return 0
        if os.environ.get("IFS_SKIP_PREFLIGHT") == "1":
            say("preflight: DID NOT RUN: IFS_SKIP_PREFLIGHT=1 is set; CI will run the checks job anyway")
            return 0
    try:
        env, steps = read_job((ROOT / WORKFLOW).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        say(f"preflight: cannot read {WORKFLOW}: {exc}; failing closed")
        return 2
    problems = parity_problems(env, steps)
    if "--check-parity" in args or "--list" in args:
        for s in steps:
            how = "declared: " + DECLARED.get(s["uses"].split("@")[0], "none") if "uses" in s else "run with bash"
            if "--list" in args:
                say(f"preflight: {s['name']}: {how}")
        for p in problems:
            say(f"preflight: parity: {p}")
        say(f"preflight: parity: {len(steps)} step(s), {len(problems)} problem(s)")
        return 1 if problems else 0
    if problems:
        for p in problems:
            say(f"preflight: parity: {p}")
        return 2
    why = ready(pushed)
    if why:
        say(f"preflight: DID NOT RUN: {why}")
        return 1 if in_ci() else 3
    return rehearse(steps, env, "--fast" in args, STEP_TIMEOUT)


# --------------------------------------------------------------------------- selftest

SAMPLE = """jobs:
  checks:
    runs-on: ubuntu-latest
    env:
      BASE_REF: main
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: One
        run: |
          echo "$BASE_REF"
          python3 -I x.py
      - name: Two
        if: ${{ !cancelled() }}
        run: python3 -I y.py

  other:
    runs-on: ubuntu-latest
"""


def selftest() -> int:
    failures = []
    env, steps = read_job(SAMPLE)
    if [s["name"] for s in steps] != ["actions/checkout@v4", "actions/setup-python@v5", "One", "Two"]:
        failures.append(f"reader: steps {[s['name'] for s in steps]}")
    if steps[2]["run"] != 'echo "$BASE_REF"\npython3 -I x.py\n' or env != {"BASE_REF": "main"}:
        failures.append("reader: run block or env")
    if parity_problems(env, steps):
        failures.append(f"parity on the clean sample: {parity_problems(env, steps)}")
    broken = {
        "unknown if": SAMPLE.replace("${{ !cancelled() }}", "${{ always() }}"),
        "hidden failure": SAMPLE.replace("python3 -I y.py", "python3 -I y.py || true"),
        "unknown variable": SAMPLE.replace('echo "$BASE_REF"', 'echo "$SECRET_TOKEN"'),
        "unknown action": SAMPLE.replace("actions/checkout@v4", "actions/cache@v4"),
        "stale declaration": SAMPLE.replace("      - uses: actions/setup-python@v5\n        with:\n          python-version: \"3.12\"\n", ""),
    }
    for label, text in broken.items():
        if not parity_problems(*read_job(text)):
            failures.append(f"parity missed: {label}")
    for label, text in {"continue-on-error": SAMPLE.replace("      - name: Two\n", "      - name: Two\n        continue-on-error: true\n"),
                        "no job": SAMPLE.replace("  checks:", "  build:")}.items():
        try:
            read_job(text)
            failures.append(f"reader accepted: {label}")
        except ValueError:
            pass
    for f in failures:
        say(f"preflight: selftest: {f}")
    say(f"preflight: selftest: {'clean' if not failures else str(len(failures)) + ' failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
