"""The dated JSON digest of each day and the two catalog files a poll publishes beside it."""
from __future__ import annotations

import contextlib
import io
import unittest
import unittest.mock
from datetime import datetime, timezone

from feeder import hub, jsonio, outputs
from tests.test_hub import DAY, FakeFetcher, HubTestCase, rss_with

NEXT = "2026-10-21"


class DatedJsonTests(HubTestCase):
    def poll_day(self, day, *extra):
        return self.run_hub("poll", "--profile", "tr", "--date", day, "--state-dir", str(self.state),
                            "--out-dir", str(self.out), *extra)

    def test_a_poll_writes_the_day_as_json_with_the_same_content_as_latest(self):
        self.assertEqual(self.poll(), 0)
        folder = self.out / "tr"
        self.assertEqual((folder / f"{DAY}.json").read_bytes(), (folder / "latest.json").read_bytes())

    def test_index_and_recent_list_every_day_newest_first(self):
        self.poll()
        self.script["https://example.gov/beta.xml"] = rss_with("Proposed rule on telecom")
        self.poll_day(NEXT)
        folder = self.out / "tr"
        index = jsonio.load_json(folder / "index.json")
        self.assertEqual([d["date"] for d in index["days"]], [NEXT, DAY])
        self.assertEqual(index["latest"], NEXT)
        for row in index["days"]:
            self.assertEqual(row["items"], len(jsonio.load_json(folder / f"{row['date']}.json")["items"]))
        recent = jsonio.load_json(folder / "recent.json")
        self.assertEqual(recent["days"], [NEXT, DAY])
        self.assertEqual(len(recent["items"]), sum(d["items"] for d in index["days"]))
        self.assertEqual({i["date"] for i in recent["items"]}, {NEXT, DAY})
        root = jsonio.load_json(self.out / "index.json")
        self.assertEqual(root["profiles"], [{"profile": "tr", "latest": NEXT, "oldest": DAY, "days": 2,
                                             "items_latest": index["days"][0]["items"]}])

    def test_an_unchanged_poll_rewrites_no_catalog_file(self):
        self.poll()
        before = self.snapshot()
        later = datetime(2026, 10, 20, 18, 0, 0, tzinfo=timezone.utc)
        with contextlib.redirect_stdout(io.StringIO()):
            hub.main(["poll", "--profile", "tr", "--date", DAY, "--state-dir", str(self.state), "--out-dir", str(self.out)],
                     root=self.root, fetcher_factory=lambda d: FakeFetcher(self.script), now_fn=lambda: later)
        self.assertEqual(self.snapshot(), before)

    def test_an_old_day_that_cannot_be_rendered_does_not_stop_the_publish(self):
        self.poll()
        (self.out / "tr" / f"{DAY}.json").unlink()
        real = hub.render_day

        def render(store, repo, profile, sources, day, *rest, **kw):
            if day == DAY:
                raise ValueError("old row")
            return real(store, repo, profile, sources, day, *rest, **kw)
        with unittest.mock.patch.object(hub, "render_day", render), unittest.mock.patch.object(hub, "warn") as warned:
            self.assertEqual(self.poll_day(NEXT, "--offline"), 0)
        self.assertTrue(any(f"tr {DAY}: JSON not filled, ValueError: old row" in c.args[0] for c in warned.call_args_list))
        self.assertFalse((self.out / "tr" / f"{DAY}.json").exists())
        self.assertEqual(jsonio.load_json(self.out / "tr" / "latest.json")["date"], NEXT)
        self.assertEqual([d["date"] for d in jsonio.load_json(self.out / "tr" / "index.json")["days"]], [NEXT])

    def test_a_day_published_without_json_is_filled_from_the_store_and_its_markdown_is_kept(self):
        self.poll()
        folder = self.out / "tr"
        (folder / f"{DAY}.json").unlink()
        (folder / f"{DAY}.md").write_text("as published\n", encoding="utf-8")
        self.poll_day(NEXT, "--offline")
        self.assertTrue((folder / f"{DAY}.json").exists())
        self.assertEqual((folder / f"{DAY}.md").read_text(encoding="utf-8"), "as published\n")
        self.assertEqual([d["date"] for d in jsonio.load_json(folder / "index.json")["days"]], [NEXT, DAY])

    def test_a_filled_day_lists_the_sources_its_own_poll_recorded(self):
        self.poll()                                   # alpha answers; beta fails with HTTP 503
        self.add_source("beta", "https://example.gov/beta.xml", ["regulation/federal-rulemaking"], enabled=False)
        self.add_source("gamma", "https://example.gov/gamma.xml", ["cybersecurity"])
        (self.out / "tr" / f"{DAY}.json").unlink()
        self.poll_day(NEXT, "--offline")
        filled = jsonio.load_json(self.out / "tr" / f"{DAY}.json")
        self.assertEqual([h["feed_id"] for h in filled["health"]], ["alpha", "beta"])
        self.assertEqual(filled["health"][1]["http_status"], 503)
        self.assertEqual((filled["coverage"]["sources_failed"], filled["coverage"]["sources_did_not_run"]), (1, 0))
        today = jsonio.load_json(self.out / "tr" / f"{NEXT}.json")
        self.assertEqual([h["feed_id"] for h in today["health"]], ["alpha", "gamma"])

    def test_turning_json_output_off_stops_the_catalog_and_the_root_index_entry(self):
        self.poll()
        folder = self.out / "tr"
        index = (folder / "index.json").read_bytes()
        (folder / "recent.json").unlink()
        self.profile["outputs"] = ["md"]
        jsonio.dump_json(self.root / "profiles" / "tr.json", self.profile)
        self.poll_day(NEXT, "--offline")
        self.assertTrue((folder / f"{NEXT}.md").exists())
        self.assertFalse((folder / f"{NEXT}.json").exists())
        self.assertFalse((folder / "recent.json").exists())
        self.assertEqual((folder / "index.json").read_bytes(), index)   # left as published, no longer listed
        self.assertEqual(jsonio.load_json(self.out / "index.json")["profiles"], [])

    def test_a_json_only_profile_keeps_latest_on_its_newest_day(self):
        self.profile.update(outputs=["json"], backfill_days=2)
        jsonio.dump_json(self.root / "profiles" / "tr.json", self.profile)
        self.poll()
        self.poll_day(NEXT, "--offline")
        folder = self.out / "tr"
        latest = (folder / "latest.json").read_bytes()
        self.assertEqual(latest, (folder / f"{NEXT}.json").read_bytes())
        later = datetime(2026, 10, 21, 18, 0, 0, tzinfo=timezone.utc)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            hub.main(["poll", "--profile", "tr", "--date", NEXT, "--state-dir", str(self.state), "--out-dir",
                      str(self.out), "--offline"], root=self.root, fetcher_factory=lambda d: FakeFetcher(self.script),
                     now_fn=lambda: later)
        self.assertEqual((folder / "latest.json").read_bytes(), latest)

    def test_a_profile_without_json_output_gets_no_json_files(self):
        self.profile["outputs"] = ["md"]
        jsonio.dump_json(self.root / "profiles" / "tr.json", self.profile)
        self.poll()
        names = {p.name for p in (self.out / "tr").iterdir()}
        self.assertEqual(names, {f"{DAY}.md", "latest.md", "report.json"})
        self.assertFalse((self.out / "index.json").exists())

    def test_dry_run_writes_nothing(self):
        self.poll("--dry-run")
        self.assertFalse(self.out.exists() and any(self.out.rglob("*.json")))

    def test_a_first_dry_run_lists_the_catalog_files_it_would_write(self):
        # Nothing is on disk yet, so the catalog can only come from the files this run would write.
        self.poll("--dry-run")
        planned = [line.rsplit(" ", 1)[-1] for line in self.output.splitlines() if "would write" in line]
        for tail in ("/tr/index.json", "/tr/recent.json", "/tr/" + DAY + ".json"):
            self.assertTrue(any(p.endswith(tail) for p in planned), tail)
        self.assertTrue(any(p.endswith("/index.json") and not p.endswith("/tr/index.json") for p in planned))

    def test_a_render_error_on_the_current_day_stops_the_poll(self):
        def render(*args, **kwargs):
            raise ValueError("render failed")
        with unittest.mock.patch.object(hub, "render_day", render):
            with self.assertRaises(ValueError):
                self.poll()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.out = Path(tempfile.mkdtemp())
        self.folder = self.out / "p"
        self.folder.mkdir()
        self.addCleanup(__import__("shutil").rmtree, self.out, True)

    def day(self, day, n=1):
        jsonio.dump_json(self.folder / f"{day}.json", {"date": day, "items": [{"content_sha256": f"{day}-{k}"} for k in range(n)]})

    def test_recent_holds_only_the_newest_days_and_the_index_holds_all(self):
        for k in range(1, outputs.RECENT_DAYS + 6):
            self.day(f"2026-{1 + k // 28:02d}-{1 + k % 28:02d}")
        files = outputs.plan_catalog(self.out, {"name": "p"}, "2026-12-01T00:00:00Z")
        index = __import__("json").loads(files[self.folder / "index.json"])
        recent = __import__("json").loads(files[self.folder / "recent.json"])
        self.assertEqual(len(index["days"]), outputs.RECENT_DAYS + 5)
        self.assertEqual(recent["days"], [d["date"] for d in index["days"]][:outputs.RECENT_DAYS])
        self.assertEqual(index["recent_days"], outputs.RECENT_DAYS)

    def test_a_file_that_is_not_a_digest_of_its_day_is_left_out(self):
        self.day("2026-10-20", 2)
        jsonio.dump_json(self.folder / "2026-10-21.json", {"date": "2026-10-19", "items": []})
        (self.folder / "2026-10-22.json").write_text("{not json", encoding="utf-8")
        jsonio.dump_json(self.folder / "2026-10-23.json", ["a list"])
        self.assertEqual(sorted(outputs.dated_json(self.folder)), ["2026-10-20"])

    def test_nothing_published_gives_no_catalog(self):
        self.assertEqual(outputs.plan_catalog(self.out, {"name": "p"}, "2026-12-01T00:00:00Z"), {})
        self.assertEqual(outputs.plan_root_index(self.out, "2026-12-01T00:00:00Z"), {})


if __name__ == "__main__":
    unittest.main()
