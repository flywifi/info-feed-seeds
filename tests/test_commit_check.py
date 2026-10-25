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
from unittest import mock

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import commit_check as cc  # noqa: E402
import doctor  # noqa: E402

FLYWIFI = {"GIT_AUTHOR_NAME": cc.AUTHOR[0], "GIT_AUTHOR_EMAIL": cc.AUTHOR[1],
           "GIT_COMMITTER_NAME": cc.AUTHOR[0], "GIT_COMMITTER_EMAIL": cc.AUTHOR[1]}
WEB = {"GIT_COMMITTER_NAME": cc.WEB_COMMITTER[0], "GIT_COMMITTER_EMAIL": cc.WEB_COMMITTER[1]}
CO = "Co-" + "Auth" + "ored-By"
NAME = "Cla" + "ude"
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
        for key in ("IFS_SKIP_HOOKS", "BASE_REF", *cc.git_local_env_names()):
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
            "spaces": "Co auth" + "ored by: x",
            "underscores": "co_auth" + "ored_by: x",
            "cyrillic o": "C\u043e-Auth" + "ored-By: x",
            "zero-width space": "Co-Auth\u200bored-By: x",
            "fullwidth host": "https://\uff43\uff4c\uff41\uff55\uff44\uff45\uff0e\uff41\uff49/x",
            "percent host": "https://claude%2E" + "ai/x",
            "entity host": "https://claude&#46;" + "ai/x",
            "italic footer": "_" + GEN + " b" + "y [Claude Code](https://example.com)_",
            "using form": GEN + " using " + "Cla" + "ude",
            "session spaces": "claude sess" + "ion: x",
            "any tool footer": GEN + " with SomeTool",
        }
        for label, text in variants.items():
            with self.subTest(variant=label):
                self.assertTrue(cc.text_findings(text, "m"), label)

    def test_ordinary_text_passes(self):
        for text in ("Writ" + "ten by info-feed-seeds hub poll", "Add the co-author rule to the docs",
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
        # A sentence that names the rules is prose, not a trailer, link or footer line.
        text = ("- Commit messages never contain a\n  " + SESSION + " line, a session or " + HOST
                + " link, a \"" + GEN + " with\" footer, or a " + CO + " line.\n  Strip any that a tool adds.\n")
        self.assertEqual(cc.content_findings("RULES.md", text.encode()), [])

    def test_this_repository_passes_the_file_rules(self):
        for rel in ("AGENTS.md", "CLAUDE.md", "README.md", "CHANGELOG.md", "tools/commit_check.py",
                    "tests/test_commit_check.py", ".github/workflows/ci.yml", "tools/hooks/_common.sh"):
            with self.subTest(path=rel):
                self.assertEqual(cc.content_findings(rel, (ROOT / rel).read_bytes()), [])

    def test_seed_files_keep_imported_bylines_on_data_lines(self):
        # A seed file imports public listings, whose own bylines may stand; prose there is still read.
        imported = ("- Research blog by " + NAME + " (https://example.org/feed.xml)\n",
                    "  - Posts by " + NAME + "\n",
                    "1. Blog by " + NAME + "\n",
                    "| Research blog by " + NAME + " | https://example.org/feed.xml |\n",
                    "|Blog by " + NAME + "|url|\n",
                    "    \"title\": \"Posts by " + NAME + "\",\n",
                    "{\"title\": \"Posts by " + NAME + "\"}\n",
                    "<outline text=\"Essays by " + NAME + "\" />\n",
                    "https://example.org/feed.xml  Essays by " + NAME + "\n",
                    "title: Essays by " + NAME + "\n")
        for text in imported:
            self.assertEqual(cc.content_findings("seeds/2026-10-24_x.md", text.encode()), [], text)
        for text in imported[:5]:  # the Markdown shapes block outside seeds/; quoted values pass everywhere
            self.assertTrue(cc.content_findings("docs/x.md", text.encode()), text)
        for prose in ("Notes compiled by " + NAME + ".\n", "Essays by " + NAME + " are listed below.\n"):
            self.assertTrue(cc.content_findings("seeds/2026-10-24_x.md", prose.encode()), prose)
        # Only the byline rule relaxes: the file author's own notes and the fixed shapes block on every line.
        for own in ("- Made with " + NAME + "\n", "- Thanks to " + NAME + " for the list\n",
                    "1. " + NAME + " wrote this list\n", "- " + CO + ": x <x@example.com>\n",
                    "<!-- list compiled by " + NAME + " -->\n", "Note: feeds, by " + NAME + "\n"):
            self.assertTrue(cc.content_findings("seeds/x.md", own.encode()), own)
        self.assertEqual(cc.file_matches(imported[0], "seeds/x.md"), [])
        self.assertTrue(cc.file_matches(imported[0], "docs/x.md"))


class FingerprintTests(unittest.TestCase):
    def test_fingerprints_come_from_the_environment(self):
        import hashlib
        word = "Qwvxzt"
        digest = hashlib.sha256(word.encode()).hexdigest()
        text = "Reviewed with " + word + " today."
        with mock.patch.dict(os.environ, {cc.FINGERPRINTS_VAR: ""}):
            self.assertEqual(cc.text_findings(text, "m"), [], "an unlisted word passes")
        with mock.patch.dict(os.environ, {cc.FINGERPRINTS_VAR: digest.upper() + ", not-a-digest"}):
            self.assertTrue(cc.text_findings(text, "m"), "a listed word blocks; a malformed entry is ignored")
            self.assertEqual(cc.text_findings(text.lower(), "m"), [], "only the capitalised spelling counts")
            self.assertEqual(cc.text_findings("Tool " + word + "ish ran", "m"), [], "inside a longer word")
            self.assertEqual(cc.content_findings("seeds/x.md", ("- Posts by " + word + "\n").encode()), [],
                             "an imported seed line may name anyone")
            self.assertTrue(cc.content_findings("seeds/x.md", ("Compiled by " + word + ".\n").encode()))

    def test_account_name_after_a_users_folder(self):
        import hashlib
        with mock.patch.dict(os.environ, {cc.FINGERPRINTS_VAR: hashlib.sha256(b"qwvxz").hexdigest()}):
            self.assertTrue(cc.text_findings(r"see C:\Users\qwvxz\x", "m"))
            self.assertTrue(cc.text_findings("see /Users/qwvxz/x", "m"))
            self.assertTrue(cc.text_findings(r"see c:\users\qwvxz\x", "m"), "lowercase folder")
            self.assertTrue(cc.text_findings(r'{"p": "C:\\Users\\qwvxz\\x"}', "m"), "a JSON-escaped path")
            self.assertEqual(cc.text_findings("the qwvxz word alone", "m"), [], "lowercase only in a path")


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
        self.assertTrue(any("co-author trailer" in f for f in found))
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

    def test_selftest_never_touches_the_repository_git_points_at(self):
        """A hook in a linked worktree receives GIT_DIR; the selftest's scratch work stays in scratch."""
        other = Scratch()
        try:
            other.commit("README.md", b"x\n", "Add README")
            refs = ["for-each-ref", "--format=%(refname) %(objectname)"]
            before = other.git(*refs).stdout
            env = dict(os.environ, GIT_DIR=str(other.dir / ".git"))
            with tempfile.TemporaryDirectory() as td:
                proc = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "commit_check.py"), "--selftest"],
                                      cwd=td, env=env, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout.decode("ascii", "replace"))
            self.assertEqual(other.git(*refs).stdout, before)
            self.assertEqual(other.git("status", "--porcelain").stdout, b"")
        finally:
            other.close()

    def test_rehearsal_steps_get_no_variables_that_point_git_at_a_repository(self):
        import preflight
        pointed = {"GIT_DIR": "/elsewhere/.git", "GIT_INDEX_FILE": "/elsewhere/index"}
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, pointed):
            env = preflight.job_env({}, td)
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_INDEX_FILE", env)


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
        self.repo.env["IFS_SKIP_PREFLIGHT"] = "1"  # the rehearsal has its own tests; these test the checks
        self.repo.env.pop("CI", None)  # hooks run on a PC, not in CI, where the skip variable is refused
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
        self.assertIn(b"blocks the merge there instead of here", result.stderr)

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
        job = f"refs/heads/main {head} refs/heads/job/x {cc.ZERO_SHA}\n".encode()
        self.assertNotEqual(self.pre_push("https://github.com/someone/else", job).returncode, 0, "foreign URL")
        self.assertNotEqual(self.pre_push("/tmp/bare/flywifi/info-feed-seeds.git", job).returncode, 0,
                            "a local path that merely contains the slug")
        no_base = self.pre_push(url, job)
        self.assertNotEqual(no_base.returncode, 0, "a new branch with no base fails closed")
        self.assertIn(b"no base to compare against", no_base.stdout)
        self.repo.git("update-ref", "refs/remotes/origin/main", head)
        self.assertNotEqual(self.pre_push(url, job).returncode, 0, "nothing new on the branch: empty range")
        tag = f"refs/tags/v1 {head} refs/tags/v1 {cc.ZERO_SHA}\n".encode()
        result = self.pre_push(url, tag)
        self.assertNotEqual(result.returncode, 0, "tags are made by the maintainer on GitHub")
        self.assertIn(b"tags are made by the maintainer", result.stdout)
        delete = f"(delete) {cc.ZERO_SHA} refs/heads/other/old {head}\n".encode()
        self.assertEqual(self.pre_push(url, delete).returncode, 0, "deleting any job branch is allowed")
        self.repo.commit("f.txt", b"f\n", "Add f")
        tip = self.repo.head()
        for name in ("other/x", "feature-x", "jobs/x", "job/"):
            other = f"refs/heads/main {tip} refs/heads/{name} {cc.ZERO_SHA}\n".encode()
            result = self.pre_push(url, other)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn(b"job/<name> or doctor/<fix>", result.stdout, name)
        for ref in ("refs/notes/commits", "refs/notes", "refs/sessions/abc", "refs/meta/config", "refs/remotes/x/y"):
            other = f"{ref} {tip} {ref} {cc.ZERO_SHA}\n".encode()
            result = self.pre_push(url, other)
            self.assertNotEqual(result.returncode, 0, ref)
            self.assertIn(b"only job/<name> and doctor/<fix> branches are pushed", result.stdout, ref)
        delete_notes = f"(delete) {cc.ZERO_SHA} refs/notes/commits {tip}\n".encode()
        self.assertEqual(self.pre_push(url, delete_notes).returncode, 0, "deleting stray notes is allowed")
        for name in ("job/x", "doctor/fix-1", "job/a.b_c/d"):
            job = f"refs/heads/main {tip} refs/heads/{name} {cc.ZERO_SHA}\n".encode()
            clean = self.pre_push(url, job)
            self.assertEqual(clean.returncode, 0, name + ": " + clean.stdout.decode("ascii", "replace"))
            self.assertIn(b"1 commit(s)", clean.stdout, "the new commit was checked")


class PrePushRangeTests(unittest.TestCase):
    def test_pre_push_checks_the_whole_branch(self):
        """A commit already pushed is still checked: pre-push takes the range CI checks."""
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            repo.git("remote", "add", "origin", "https://github.com/flywifi/info-feed-seeds")
            repo.git("update-ref", "refs/remotes/origin/main", repo.head())
            repo.commit("a.txt", b"a\n", "Add a\n\n" + CO + ": X <x@example.com>")
            pushed = repo.head()
            repo.commit("b.txt", b"b\n", "Add b")
            line = f"refs/heads/job {repo.head()} refs/heads/job/x {pushed}"
            found = quiet(cc.check_pre_push, "origin", "https://github.com/flywifi/info-feed-seeds", [line],
                          cwd=repo.dir)
            self.assertTrue(any(pushed[:7] in f and "co-author trailer" in f for f in found), found)
        finally:
            repo.close()

    @unittest.skipIf(shutil.which("sh") is None, "the hooks are sh scripts")
    def test_pre_push_runs_the_rehearsal(self):
        """Without IFS_SKIP_PREFLIGHT the hook runs tools/preflight.py; this scratch copy has no
        ci.yml, so the rehearsal fails closed and the push is refused."""
        repo = Scratch(hooks=True)
        try:
            repo.commit("README.md", b"x\n", "Add README")
            repo.git("remote", "add", "origin", "https://github.com/flywifi/info-feed-seeds")
            repo.git("update-ref", "refs/remotes/origin/main", repo.head())
            repo.commit("f.txt", b"f\n", "Add f")
            job = f"refs/heads/main {repo.head()} refs/heads/job/x {cc.ZERO_SHA}\n".encode()
            hook = str(repo.dir / "tools" / "hooks" / "pre-push")
            result = subprocess.run(["sh", hook, "origin", "https://github.com/flywifi/info-feed-seeds"],
                                    cwd=repo.dir, env=repo.env, input=job, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"cannot read .github/workflows/ci.yml", result.stdout)
        finally:
            repo.close()


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



# Footer, folding and range cases, assembled from pieces so this file never holds them.
ITALIC_FOOTER = "_" + GEN + " with_ [" + "Cla" + "ude Code](...)"


class FooterAndFoldingTests(unittest.TestCase):
    def test_italic_footer_everywhere(self):
        self.assertTrue(cc.text_findings("Add a\n\n" + ITALIC_FOOTER, "m"), "commit message")
        self.assertTrue(quiet(cc.check_pr_text, "Job", "Body\n\n" + ITALIC_FOOTER, "pr"), "pull request body")
        self.assertTrue(cc.content_findings("notes.md", (ITALIC_FOOTER + "\n").encode()), "file")

    def test_italic_footer_in_main_history(self):
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            base = repo.head()
            repo.commit("m.txt", b"m\n", "Merge pull request 9\n\n" + ITALIC_FOOTER, extra=WEB)
            found = quiet(cc.check_range, f"{base}..HEAD", branch="main", cwd=repo.dir,
                          main_history=True, verify=lambda sha: True)
            self.assertTrue(any("generated footer" in f for f in found), found)
        finally:
            repo.close()

    def test_lookalikes_and_hidden_characters(self):
        for key in ("trailer capital cyrillic", "trailer capital cyrillic o", "session capital greek",
                    "trailer combining mark", "trailer accented letter", "trailer hangul filler",
                    "link ideographic dot", "link halfwidth dot", "link fullwidth dot"):
            with self.subTest(probe=key):
                self.assertTrue(cc.text_findings(SAMPLES[key], "m"), key)
        for filler in ("\u3164", "\u115f", "\u1160", "\uffa0"):
            with self.subTest(filler=hex(ord(filler))):
                self.assertTrue(cc.text_findings("Co-Auth" + filler + "ored-By: x", "m"))
        for dot in ("\u3002", "\uff61", "\uff0e"):
            with self.subTest(dot=hex(ord(dot))):
                self.assertTrue(cc.content_findings("n.md", ("https://claude" + dot + "ai/x\n").encode()))

    def test_footer_line_naming_any_tool(self):
        """A line holding only the footer phrase and a tool is refused whatever tool it names."""
        self.assertTrue(cc.text_findings(GEN + " with hub poll", "m"))

    def test_dash_entities_and_more_dashes(self):
        for text in ("a &" + "mdash; b", "a &" + "ndash; b", "a &#" + "8212; b", "a \u2015 b", "a \u2e3a b",
                     "a \u2e3b b", "a \ufe58 b", "a \ufe31 b"):
            with self.subTest(text=text.encode("ascii", "backslashreplace")):
                self.assertTrue(cc.text_findings(text, "m"))
                self.assertTrue(cc.content_findings("d.md", (text + "\n").encode()))

    def test_pre_push_with_no_or_malformed_lines(self):
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            repo.git("remote", "add", "origin", "https://github.com/flywifi/info-feed-seeds")
            url = "https://github.com/flywifi/info-feed-seeds"
            none = quiet(cc.check_pre_push, "origin", url, [], cwd=repo.dir)
            self.assertTrue(any("no refs" in f for f in none), none)
            blank = quiet(cc.check_pre_push, "origin", url, ["", "   "], cwd=repo.dir)
            self.assertTrue(any("no refs" in f for f in blank), blank)
            bad = quiet(cc.check_pre_push, "origin", url, ["refs/heads/x abc refs/heads/x def"], cwd=repo.dir)
            self.assertTrue(any("malformed" in f for f in bad), bad)
        finally:
            repo.close()

    def test_main_history_web_committer(self):
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            base = repo.head()
            repo.commit("w.txt", b"w\n", "Web edit on main", extra=WEB)
            forged = repo.head()
            unverified = quiet(cc.check_range, f"{base}..HEAD", branch="main", cwd=repo.dir,
                               main_history=True, verify=lambda sha: False)
            self.assertTrue(any("committer" in f for f in unverified), "a non-merge web commit needs verification")
            verified = quiet(cc.check_range, f"{base}..HEAD", branch="main", cwd=repo.dir,
                             main_history=True, verify=lambda sha: sha == forged)
            self.assertEqual(verified, [])
            repo.git("checkout", "-q", "-b", "side", base)
            repo.commit("s.txt", b"s\n", "Side work")
            repo.git("checkout", "-q", "main")
            repo.git("merge", "-q", "--no-ff", "side", "-m", "Merge pull request 3", extra=WEB)
            merged = quiet(cc.check_range, f"{forged}..HEAD", branch="main", cwd=repo.dir,
                           main_history=True, verify=lambda sha: False)
            self.assertTrue(any("committer" in f for f in merged),
                            "a merge by GitHub needs verification too")
            verified_merge = quiet(cc.check_range, f"{forged}..HEAD", branch="main", cwd=repo.dir,
                                   main_history=True, verify=lambda sha: True)
            self.assertEqual(verified_merge, [])
        finally:
            repo.close()


@unittest.skipIf(shutil.which("sh") is None, "the hooks are sh scripts")
class CommitterHookTests(unittest.TestCase):
    def test_pre_commit_checks_the_committer(self):
        repo = Scratch(hooks=True)
        try:
            repo.commit("README.md", b"x\n", "Add README")
            result = repo.commit("c.txt", b"c\n", "Add c", check=False,
                                 extra={"GIT_COMMITTER_NAME": "Someone", "GIT_COMMITTER_EMAIL": "s@example.com"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"committer for this commit", result.stdout + result.stderr)
        finally:
            repo.close()


# Footer shapes, assembled from pieces; "(robot)" is the text form, and the emoji form is tested as well.
CL = "Cla" + "ude"
FOOTER_SHAPES = {
    "sub robot": "<sub>(robot) " + GEN + " with [" + CL + " Code](https://" + CL.lower() + ".com/"
                 + CL.lower() + "-code)</sub>",
    "sub robot emoji": "<sub>\U0001F916 " + GEN + " with [" + CL + " Code](https://" + CL.lower() + ".com/"
                       + CL.lower() + "-code)</sub>",
    "note label": "Note: " + GEN + " with [...]",
    "sentence": "This PR was " + GEN.lower() + " with " + CL + " Code",
    "html p": '<p align="center">' + GEN + " with " + CL + " Code</p>",
    "bold label": "**Tooling:** " + GEN + " by " + CL,
    "version prefix": "v2 " + GEN + " using " + CL + " Code",
}


class FooterShapeAndKnownCommitTests(unittest.TestCase):
    def test_footer_shapes_are_refused_everywhere(self):
        for label, text in FOOTER_SHAPES.items():
            with self.subTest(footer=label):
                self.assertTrue(cc.text_findings("Add a\n\n" + text, "m"), "commit message")
                self.assertTrue(quiet(cc.check_pr_text, "Job", "Body\n\n" + text, "pr"), "pull request body")
                self.assertTrue(cc.content_findings("notes.md", (text + "\n").encode()), "file")

    def test_footer_shapes_in_main_history(self):
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            base = repo.head()
            for label, text in FOOTER_SHAPES.items():
                repo.commit(label.replace(" ", "-") + ".txt", b"x\n", "Merge pull request 1\n\n" + text, extra=WEB)
            found = quiet(cc.check_range, f"{base}..HEAD", branch="main", cwd=repo.dir,
                          main_history=True, verify=lambda sha: True)
            self.assertEqual(len({f.split()[1] for f in found if "generated footer" in f}), len(FOOTER_SHAPES), found)
        finally:
            repo.close()

    def test_prose_about_coauthorship_passes(self):
        prose = "Cite the paper co-" + "authored by Smith"
        self.assertEqual(cc.text_findings(prose, "m"), [])
        self.assertTrue(cc.text_findings("Add a\n\n" + CO + ": Smith <s@example.com>", "m"), "the trailer still fails")

    def test_known_commits_on_main(self):
        """An acknowledgement counts only when it follows the landing."""
        repo = Scratch()
        try:
            known = "tools/main_history_known.txt"
            repo.commit("README.md", b"x\n", "Add README")
            base = repo.head()

            def history():
                repo.git("update-ref", "refs/remotes/origin/main", repo.head())
                return loud(cc.check_range, f"{base}..HEAD", branch="main", cwd=repo.dir, main_history=True,
                            verify=lambda sha: True)

            repo.commit("m.txt", b"m\n", "Merge pull request 5\n\n" + SESSION + ": abc", extra=WEB)
            bad = repo.head()
            found, _ = history()
            self.assertTrue(found, "an unacknowledged bad landing fails")
            # A later pull request acknowledges it, merged through a merge commit as on GitHub.
            repo.git("checkout", "-q", "-b", "ack")
            repo.commit(known, ("# acknowledged\n" + bad + " landed before the check; accepted by the owner\n").encode(),
                        "Acknowledge an earlier landing")
            repo.git("checkout", "-q", "main")
            repo.git("merge", "-q", "--no-ff", "ack", "-m", "Merge pull request 6", extra=WEB)
            found, out = history()
            self.assertEqual(found, [], "an acknowledgement that follows the landing is honoured")
            self.assertIn("known, acknowledged", out)
            # A pull request that carries a bad commit and acknowledges it in the same change.
            repo.git("checkout", "-q", "-b", "sneak")
            repo.commit("s.txt", b"s\n", "Sneak\n\n" + SESSION + ": def")
            sneak = repo.head()
            existing = (repo.dir / known).read_text(encoding="utf-8")
            repo.commit(known, (existing + sneak + " pre-acknowledged\n").encode(), "Pre-acknowledge")
            repo.git("checkout", "-q", "main")
            repo.git("merge", "-q", "--no-ff", "sneak", "-m", "Merge pull request 7", extra=WEB)
            found, _ = history()
            self.assertTrue(any("same change that brought it" in f for f in found), found)
            self.assertTrue(any(sneak[:7] in f and "session trailer" in f for f in found), found)
        finally:
            repo.close()

    def test_known_commits_malformed_and_foreign_entries(self):
        repo = Scratch()
        try:
            repo.commit("README.md", b"x\n", "Add README")
            base = repo.head()
            repo.commit("tools/main_history_known.txt", ("abc not-a-sha\n" + "f" * 40 + " not in range\n").encode(),
                        "Bad list")
            repo.git("update-ref", "refs/remotes/origin/main", repo.head())
            found = quiet(cc.check_range, f"{base}..HEAD", branch="main", cwd=repo.dir, main_history=True,
                          verify=lambda sha: True)
            self.assertTrue(any("40-character sha" in f for f in found), found)
            self.assertTrue(any("not a commit in the range" in f for f in found), found)
        finally:
            repo.close()

if __name__ == "__main__":
    unittest.main()
