"""tools/commit_check.py, the tracked hooks and the doctor, against scratch git repositories.

Every banned string here is assembled at run time ("Co-" + "Authored-By" and so on), so this file
never contains what the checker hunts for."""
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
import doctor  # noqa: E402

FLYWIFI = {"GIT_AUTHOR_NAME": cc.AUTHOR[0], "GIT_AUTHOR_EMAIL": cc.AUTHOR[1],
           "GIT_COMMITTER_NAME": cc.AUTHOR[0], "GIT_COMMITTER_EMAIL": cc.AUTHOR[1]}
WEB = {"GIT_COMMITTER_NAME": cc.WEB_COMMITTER[0], "GIT_COMMITTER_EMAIL": cc.WEB_COMMITTER[1]}
CO = "Co-" + "Authored-By"
SESSION = "Claude" + "-Session"
HOST = "claude" + ".ai"
GEN = "Gener" + "ated"
SAMPLES = cc.banned_samples()


def quiet(fn, *args, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*args, **kw)


def loud(fn, *args, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kw)
    return result, buf.getvalue()


class Scratch:
    """A throwaway repository with flywifi as the default identity and no global config."""

    def __init__(self, hooks=False):
        self.dir = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **FLYWIFI)
        for key in ("IFS_SKIP_HOOKS", "BASE_REF"):
            self.env.pop(key, None)
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        if hooks:
            shutil.copytree(ROOT / "tools", self.dir / "tools", ignore=shutil.ignore_patterns("__pycache__"))
            self.git("config", "core.hooksPath", "tools/hooks")
        else:
            self.git("config", "core.hooksPath", os.devnull)

    def git(self, *args, extra=None, check=True, stdin=None):
        return subprocess.run(["git", *args], cwd=self.dir, env={**self.env, **(extra or {})},
                              capture_output=True, check=check, input=stdin)

    def commit(self, name, data, message, extra=None, check=True, args=()):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.git("add", name)
        return self.git("commit", "-q", *args, "-m", message, extra=extra, check=check)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TextTests(unittest.TestCase):
    def test_every_planted_sample_is_refused(self):
        for label, text in SAMPLES.items():
            with self.subTest(sample=label):
                self.assertTrue(cc.text_findings(text, "m"), label)

    def test_unicode_and_spacing_variants(self):
        variants = {
            "U+2011 hyphens": "co\u2011authored\u2011by: x",
            "spaces": "Co authored by: x",
            "underscores": "co_authored_by: x",
            "cyrillic o": "C\u043e-Authored-By: x",
            "zero-width space": "Co-Auth\u200bored-By: x",
            "fullwidth host": "https://\uff43\uff4c\uff41\uff55\uff44\uff45\uff0e\uff41\uff49/x",
            "percent host": "https://claude%2E" + "ai/x",
            "entity host": "https://claude&#46;" + "ai/x",
            "italic footer": "_" + GEN + " by [Claude Code](https://example.com)_",
            "using form": GEN + " using " + "Cla" + "ude",
            "session spaces": "claude session: x",
            "any tool footer": GEN + " with SomeTool",
        }
        for label, text in variants.items():
            with self.subTest(variant=label):
                self.assertTrue(cc.text_findings(text, "m"), label)

    def test_ordinary_text_passes(self):
        for text in ("Written by info-feed-seeds hub poll", "Add the co-author rule to the docs",
                     "Generated data for the digest", "Claude reviewed it"):
            self.assertEqual(cc.text_findings(text, "m"), [], text)

    def test_dashes(self):
        self.assertTrue(cc.text_findings("a \u2013 b", "m"))
        self.assertTrue(cc.text_findings("a \u2014 b", "m"))


class IdentityTests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(cc.identity_findings(cc.AUTHOR, cc.AUTHOR, "c"), [])
        self.assertTrue(cc.identity_findings(("x", "x@example.com"), cc.AUTHOR, "c"))
        self.assertTrue(cc.identity_findings(cc.AUTHOR, ("x", "x@example.com"), "c"))

    def test_web_committer_only_on_main(self):
        self.assertEqual(cc.identity_findings(cc.AUTHOR, cc.WEB_COMMITTER, "c", on_main=True), [])
        found = cc.identity_findings(cc.AUTHOR, cc.WEB_COMMITTER, "c", on_main=False)
        self.assertTrue(found)
        self.assertIn("not on main", found[0])


class FileTests(unittest.TestCase):
    def test_paths(self):
        for bad in ("local/a", ".cache/x.db", "logs/a.log", ".venv-crawl/x", "profiles/p.local.json",
                    "a/.secrets.local.json"):
            self.assertTrue(cc.path_findings(bad, "job"), bad)
        self.assertTrue(cc.path_findings("digests/p/latest.md", "job"))
        self.assertEqual(cc.path_findings("digests/p/latest.md", "data"), [])
        self.assertEqual(cc.path_findings("feeder/hub.py", "job"), [])

    def test_content_basics(self):
        self.assertTrue(cc.content_findings("a.txt", b"x\r\n"))
        self.assertTrue(cc.content_findings("a.txt", b"\xff\xfe bad"))
        self.assertTrue(cc.content_findings("a.md", "x \u2014 y".encode()))
        self.assertTrue(cc.content_findings("a.py", "s = '\u00e9'\n".encode()))
        self.assertEqual(cc.content_findings("a.md", "caf\u00e9\n".encode()), [], "non-ASCII is fine outside .py")
        self.assertEqual(cc.content_findings("a.bin", b"\x00\x01\r\n"), [], "binary skipped")

    def test_banned_lines_and_links_in_files(self):
        planted = {
            "trailer line": (CO + ": Someone <x@example.com>\n").encode(),
            "session line": (SESSION + ": abc\n").encode(),
            "link": ("see https://" + HOST + "/code/session_x\n").encode(),
            "bare host path": (HOST + "/code/x\n").encode(),
            "footer line": ("\U0001F916 " + GEN + " with [Claude Code](https://example.com)\n").encode(),
            "any tool footer": (GEN + " with SomeTool\n").encode(),
        }
        for label, data in planted.items():
            with self.subTest(planted=label):
                self.assertTrue(cc.content_findings("notes.md", data), label)

    def test_a_sentence_naming_the_rules_passes(self):
        text = ("- Commit messages never contain a\n  " + CO + " line, a " + SESSION + " line, a session or "
                + HOST + " link, or a \"" + GEN + " with\" footer. Strip\n")
        self.assertEqual(cc.content_findings("RULES.md", text.encode()), [])

    def test_this_repository_passes_the_file_rules(self):
        for rel in ("CLAUDE.md", "README.md", "CHANGELOG.md", "tools/commit_check.py",
                    "tests/test_commit_check.py", ".github/workflows/ci.yml", "tools/hooks/_common.sh"):
            with self.subTest(path=rel):
                self.assertEqual(cc.content_findings(rel, (ROOT / rel).read_bytes()), [])


class MessageFileTests(unittest.TestCase):
    def test_comment_lines_are_checked(self):
        hidden = "Add a\n\n# " + CO + ": x\n"
        self.assertTrue(cc.check_message_text(hidden), "a '#' line survives --cleanup=verbatim")

    def test_text_below_the_scissors_is_ignored(self):
        text = "Add a\n" + cc.SCISSORS + "\ndiff --git a/x b/x\n+" + CO + ": x\n"
        self.assertEqual(cc.check_message_text(text), [])


class PushUrlTests(unittest.TestCase):
    def test_exact_repository(self):
        for good in ("https://github.com/flywifi/info-feed-seeds", "https://github.com/flywifi/info-feed-seeds.git",
                     "git@github.com:flywifi/info-feed-seeds.git", "ssh://git@github.com/flywifi/info-feed-seeds"):
            self.assertTrue(cc.remote_names_repo(good), good)
        for bad in ("/tmp/x/flywifi/info-feed-seeds.git", "https://evil.example/flywifi/info-feed-seeds",
                    "https://github.com/flywifi/info-feed-seeds-fork", "https://github.com/other/info-feed-seeds",
                    "DISABLED_READ_ONLY"):
            self.assertFalse(cc.remote_names_repo(bad), bad)


class PrEventTests(unittest.TestCase):
    def write(self, obj):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(obj, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_empty_payload_fails_closed(self):
        self.assertTrue(cc.check_pr_event(self.write({})))
        self.assertTrue(cc.check_pr_event(self.write({"pull_request": {"title": "x"}})))

    def test_missing_file_is_reported(self):
        found = cc.check_pr_event("/nonexistent/event.json")
        self.assertTrue(found)
        self.assertIn("cannot read", found[0])

    def test_footer_and_clean_payloads(self):
        bad = {"pull_request": {"number": 7, "title": "Job", "body": SAMPLES["footer italic"]}}
        good = {"pull_request": {"number": 7, "title": "Job", "body": "A plain description."}}
        self.assertTrue(cc.check_pr_event(self.write(bad)))
        self.assertEqual(cc.check_pr_event(self.write(good)), [])


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.repo = Scratch()
        self.repo.commit("README.md", b"x\n", "Add README")
        self.base = self.repo.head()

    def tearDown(self):
        self.repo.close()

    def test_clean_range(self):
        self.repo.commit("a.txt", b"a\n", "Add a")
        self.assertEqual(quiet(cc.check_range, f"{self.base}..HEAD", cwd=self.repo.dir), [])

    def test_empty_range_fails_closed(self):
        found = quiet(cc.check_range, f"{self.base}..{self.base}", cwd=self.repo.dir)
        self.assertTrue(found)
        self.assertIn("no commits", found[0])
        self.assertTrue(quiet(cc.check_sources, f"{self.base}..{self.base}", cwd=self.repo.dir))

    def test_trailer_in_range(self):
        self.repo.commit("a.txt", b"a\n", "Add a\n\n" + CO + ": X <x@example.com>")
        self.assertTrue(quiet(cc.check_range, f"{self.base}..HEAD", cwd=self.repo.dir))

    def test_banned_line_in_a_committed_file(self):
        self.repo.commit("notes.md", (CO + ": X <x@example.com>\nhttps://" + HOST + "/code/x\n").encode(), "Add notes")
        found = quiet(cc.check_range, f"{self.base}..HEAD", cwd=self.repo.dir)
        self.assertTrue(any("trailer line" in f for f in found))
        self.assertTrue(any("session link" in f for f in found))

    def test_spoofed_github_committer(self):
        self.repo.git("update-ref", "refs/remotes/origin/main", self.base)
        self.repo.commit("w.txt", b"w\n", "Pretend web edit", extra=WEB)
        found = quiet(cc.check_range, f"{self.base}..HEAD", cwd=self.repo.dir)
        self.assertTrue(any("not on main" in f for f in found))

    def test_merge_on_main_with_footer_is_caught(self):
        self.repo.commit("m.txt", b"m\n", "Merge pull request 9\n\n" + SAMPLES["footer italic"], extra=WEB)
        self.repo.git("update-ref", "refs/remotes/origin/main", self.repo.head())
        found = quiet(cc.check_range, f"{self.base}..HEAD", branch="main", cwd=self.repo.dir)
        self.assertTrue(any("generated footer" in f for f in found))
        self.assertFalse(any("committer" in f for f in found), "the GitHub committer is fine on main")

    def test_auto_range_fails_closed(self):
        self.assertIsNone(cc.resolve_range("auto", cwd=self.repo.dir), "no origin/main and no boundary")

    def test_changing_the_checker_prints_a_notice(self):
        self.repo.commit("tools/commit_check.py", b"print('x')\n", "Edit the checker")
        found, out = loud(cc.check_range, f"{self.base}..HEAD", cwd=self.repo.dir)
        self.assertEqual(found, [])
        self.assertIn("NOTICE", out)
        self.assertIn("tools/commit_check.py", out)

    def test_a_copy_outside_the_repository_checks_the_current_directory(self):
        """CI runs the base branch's copy from $RUNNER_TEMP; it must still inspect the checkout."""
        self.repo.commit("a.txt", b"a\n", "Add a\n\n" + CO + ": X <x@example.com>")
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "commit_check.py"
            shutil.copy(ROOT / "tools" / "commit_check.py", copy)
            proc = subprocess.run([sys.executable, "-I", str(copy), "--range", f"{self.base}..HEAD"],
                                  cwd=self.repo.dir, env=self.repo.env, capture_output=True)
        self.assertEqual(proc.returncode, 1, proc.stdout.decode("ascii", "replace"))
        self.assertIn(b"co-author trailer", proc.stdout)

    def test_selftest(self):
        self.assertEqual(quiet(cc.selftest), [])


class SourcesTests(unittest.TestCase):
    def test_changed_source_needs_live_sample(self):
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            base = repo.head()
            manifest = {"routes": [{"adapter": "feed", "url": "https://example.gov/f.xml"}], "verified": "2026-10-21"}
            attempt = {"source_label": "live", "http_status": 200, "result": "ok", "tried_at": "2026-10-21T00:00:00Z",
                       "sample": None}
            methods = {"routes": [{"url": "https://example.gov/f.xml", "attempts": [attempt]}]}
            repo.commit("sources/s/source.json", json.dumps(manifest).encode(), "Add s")
            repo.commit("sources/s/methods.json", json.dumps(methods).encode(), "Add s methods")
            self.assertTrue(quiet(cc.check_sources, f"{base}..HEAD", cwd=repo.dir), "no sample")
            attempt["sample"] = {"title": "t", "link": None, "published": None}
            repo.commit("sources/s/methods.json", json.dumps(methods).encode(), "Add sample")
            self.assertEqual(quiet(cc.check_sources, f"{base}..HEAD", cwd=repo.dir), [])
        finally:
            repo.close()


@unittest.skipIf(shutil.which("sh") is None, "the hooks are sh scripts")
class HookTests(unittest.TestCase):
    def setUp(self):
        self.repo = Scratch(hooks=True)
        self.repo.commit("README.md", b"x\n", "Add README")

    def tearDown(self):
        self.repo.close()

    def pre_push(self, url, stdin):
        hook = str(self.repo.dir / "tools" / "hooks" / "pre-push")
        return subprocess.run(["sh", hook, "origin", url], cwd=self.repo.dir, env=self.repo.env,
                              input=stdin, capture_output=True)

    def test_pre_commit_refuses_crlf_and_forbidden_paths(self):
        self.assertNotEqual(self.repo.commit("a.txt", b"a\r\n", "Add a", check=False).returncode, 0)
        self.repo.git("reset", "-q")
        self.assertNotEqual(self.repo.commit("local/x.txt", b"x\n", "Add x", check=False).returncode, 0)

    def test_pre_commit_refuses_a_banned_line_in_a_file(self):
        result = self.repo.commit("notes.md", (SESSION + ": x\n").encode(), "Add notes", check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_commit_msg_refuses_trailer(self):
        result = self.repo.commit("b.txt", b"b\n", "Add b\n\n" + SESSION + ": x", check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_commit_msg_refuses_a_hidden_comment_line_under_verbatim(self):
        result = self.repo.commit("c.txt", b"c\n", "Add c\n\n# " + CO + ": x", check=False,
                                  args=("--cleanup=verbatim",))
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_author_refused(self):
        result = self.repo.commit("c.txt", b"c\n", "Add c", check=False,
                                  extra={"GIT_AUTHOR_NAME": "Someone", "GIT_AUTHOR_EMAIL": "s@example.com"})
        self.assertNotEqual(result.returncode, 0)

    def test_clean_commit_passes(self):
        self.assertEqual(self.repo.commit("d.txt", b"d\n", "Add d", check=False).returncode, 0)

    def test_bypass_is_loud_and_honest(self):
        result = self.repo.commit("e.txt", b"e\r\n", "Add e", check=False, extra={"IFS_SKIP_HOOKS": "1"})
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"IFS_SKIP_HOOKS=1 is set", result.stderr)
        self.assertIn(b"blocks a merge only once", result.stderr)

    def test_pre_push_rules(self):
        head = self.repo.head()
        self.repo.git("remote", "add", "origin", "https://github.com/flywifi/info-feed-seeds")
        url = "https://github.com/flywifi/info-feed-seeds"
        to_main = f"refs/heads/main {head} refs/heads/main {cc.ZERO_SHA}\n".encode()
        self.assertNotEqual(self.pre_push(url, to_main).returncode, 0, "main")
        to_data = f"refs/heads/main {head} refs/heads/data {cc.ZERO_SHA}\n".encode()
        result = self.pre_push(url, to_data)
        self.assertNotEqual(result.returncode, 0, "data")
        self.assertIn(b"refusing to push to data", result.stdout)
        job = f"refs/heads/main {head} refs/heads/claude/job {cc.ZERO_SHA}\n".encode()
        self.assertNotEqual(self.pre_push("https://github.com/someone/else", job).returncode, 0, "foreign URL")
        self.assertNotEqual(self.pre_push("/tmp/bare/flywifi/info-feed-seeds.git", job).returncode, 0,
                            "a local path that merely contains the slug")
        no_base = self.pre_push(url, job)
        self.assertNotEqual(no_base.returncode, 0, "a new branch with no base fails closed")
        self.assertIn(b"no base to compare against", no_base.stdout)
        self.repo.git("update-ref", "refs/remotes/origin/main", head)
        self.assertNotEqual(self.pre_push(url, job).returncode, 0, "nothing new on the branch: empty range")
        tag = f"refs/tags/v1 {head} refs/tags/v1 {cc.ZERO_SHA}\n".encode()
        self.assertNotEqual(self.pre_push(url, tag).returncode, 0, "a tag on an already-pushed commit is empty")
        delete = f"(delete) {cc.ZERO_SHA} refs/heads/claude/old {head}\n".encode()
        self.assertEqual(self.pre_push(url, delete).returncode, 0, "deleting a job branch is allowed")
        self.repo.commit("f.txt", b"f\n", "Add f")
        tip = self.repo.head()
        job = f"refs/heads/main {tip} refs/heads/claude/job {cc.ZERO_SHA}\n".encode()
        clean = self.pre_push(url, job)
        self.assertEqual(clean.returncode, 0, clean.stdout.decode("ascii", "replace"))
        self.assertIn(b"1 commit(s)", clean.stdout, "the new commit was checked")


class DoctorTests(unittest.TestCase):
    def test_doctor_check_runs_clean_on_this_repository(self):
        self.assertEqual(quiet(doctor.check), 0)

    def test_hooks_check_is_advisory(self):
        from doctor_checks import hooks
        self.assertFalse(hooks.REQUIRED)
        repo = Scratch()
        try:
            repo.git("remote", "add", "origin", "https://github.com/flywifi/info-feed-seeds")
            repo.git("config", "--unset", "core.hooksPath")
            status, message = hooks.run(repo.dir)
            self.assertEqual(status, "advisory")
            self.assertIn("--no-verify", message)
            repo.git("config", "core.hooksPath", "tools/hooks")
            self.assertEqual(hooks.run(repo.dir)[0], "ok")
            repo.git("remote", "set-url", "--push", "origin", "DISABLED_READ_ONLY")
            repo.git("config", "--unset", "core.hooksPath")
            self.assertEqual(hooks.run(repo.dir)[0], "ok", "a pull-only clone needs no hooks")
        finally:
            repo.close()

    def test_hooks_check_outside_git_is_blocked_not_ok(self):
        from doctor_checks import hooks
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(hooks.run(Path(td))[0], "blocked")

    def test_exit_codes(self):
        self.assertEqual(quiet(doctor.main, []), 2, "usage")
        self.assertEqual(quiet(doctor.main, ["fix", "--safe"]), 4, "not implemented")
        self.assertEqual(quiet(doctor.main, ["propose"]), 4, "not implemented")


if __name__ == "__main__":
    unittest.main()
