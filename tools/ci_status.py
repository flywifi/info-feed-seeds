#!/usr/bin/env python3
"""Read GitHub Actions results for one commit through the REST API (gh api, GET only), keeping data,
empty and error apart.

Usage:
  python3 tools/ci_status.py SHA        every workflow run for the commit
  python3 tools/ci_status.py --selftest

Exit 0  every run for the commit completed with conclusion success
     1  a run completed with any other conclusion (failure, cancelled, timed_out and so on)
     2  ERROR: gh failed, the answer was not JSON, or it holds no workflow_runs list; the API's own
        message is printed
     3  EMPTY or PENDING: no run for the commit yet, or a run is still queued or in progress
Report a commit as green only from exit 0. Standard library only; ASCII output.
"""
from __future__ import annotations

import json
import subprocess
import sys

REPO_SLUG = "flywifi/info-feed-seeds"


def say(message: str) -> None:
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def classify(returncode: int, body: str, stderr: str = "") -> tuple:
    """The three-state contract, pure so the selftest can pin it: ("error", message), ("empty",
    None) or ("data", runs)."""
    if returncode != 0:
        return ("error", (stderr or body).strip()[:300])
    try:
        payload = json.loads(body)
    except ValueError:
        return ("error", f"not JSON: {body[:200]}")
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return ("error", str(payload.get("message", payload) if isinstance(payload, dict) else payload)[:300])
    runs = payload["workflow_runs"]
    return ("empty", None) if not runs else ("data", runs)


def verdict(state: str, value) -> tuple:
    """(exit code, lines) for a classified answer."""
    if state == "error":
        return 2, [f"ERROR: {value}"]
    if state == "empty":
        return 3, ["EMPTY: no workflow run for this commit yet"]
    lines = [f"{r.get('name')} ({r.get('event')}): {r.get('status')} {r.get('conclusion') or '-'} {r.get('html_url', '')}"
             for r in value]
    # The newest run for each workflow and event decides: an older run that a newer one replaced
    # (the concurrency group cancels it) does not.
    newest = {}
    for r in value:
        key = (r.get("name"), r.get("event"))
        if key not in newest or (r.get("run_number", 0), r.get("run_attempt", 0)) > (
                newest[key].get("run_number", 0), newest[key].get("run_attempt", 0)):
            newest[key] = r
    judged = list(newest.values())
    failed = any(r.get("status") == "completed" and r.get("conclusion") != "success" for r in judged)
    pending = any(r.get("status") != "completed" for r in judged)
    return (1 if failed else 3 if pending else 0), lines


def fetch(sha: str) -> tuple:
    proc = subprocess.run(["gh", "api", f"repos/{REPO_SLUG}/actions/runs?head_sha={sha}&per_page=100"],
                          capture_output=True)
    return classify(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


def selftest() -> int:
    def run(status, conclusion, name="ci", number=1):
        return {"name": name, "event": "push", "status": status, "conclusion": conclusion, "run_number": number}
    cases = [
        ((1, "", "gh: Forbidden (HTTP 403)"), 2), ((0, "<html>proxy error</html>"), 2),
        ((0, json.dumps({"message": "Not Found"})), 2), ((0, json.dumps({"total_count": 0})), 2),
        ((0, json.dumps({"workflow_runs": []})), 3),
        ((0, json.dumps({"workflow_runs": [run("completed", "success")]})), 0),
        ((0, json.dumps({"workflow_runs": [run("completed", "success"), run("in_progress", None, "docs")]})), 3),
        ((0, json.dumps({"workflow_runs": [run("completed", "failure"), run("queued", None, "docs")]})), 1),
        ((0, json.dumps({"workflow_runs": [run("completed", "cancelled")]})), 1),
        ((0, json.dumps({"workflow_runs": [run("completed", "cancelled"), run("completed", "success", number=2)]})), 0),
        ((0, json.dumps({"workflow_runs": [run("completed", "failure"), run("queued", None, number=2)]})), 3),
    ]
    failures = [f"{args!r}: expected {want}" for args, want in cases if verdict(*classify(*args))[0] != want]
    for f in failures:
        say(f"ci_status: selftest: {f}")
    say(f"ci_status: selftest: {'clean' if not failures else str(len(failures)) + ' failure(s)'}")
    return 1 if failures else 0


def main(argv: list) -> int:
    if argv == ["--selftest"]:
        return selftest()
    if len(argv) != 1 or argv[0].startswith("-"):
        say(__doc__.strip())
        return 2
    code, lines = verdict(*fetch(argv[0]))
    for line in lines:
        say(f"ci_status: {line}")
    say(f"ci_status: {argv[0][:7]}: " + {0: "green", 1: "red", 2: "error, not a result", 3: "no result yet"}[code])
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
