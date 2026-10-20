"""The 30-day title fixture built by tools/build_title_fixture.py from the NIST and govinfo seed
feeds. Offline: the fixture is re-scored and must agree with the counts recorded when it was built."""
import unittest
from datetime import datetime, timedelta, timezone

from feeder import guard, jsonio
from tests.helpers import FIXTURES

FIXTURE = FIXTURES / "live" / "titles.json"


class TitleFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = jsonio.load_json(FIXTURE)

    def test_fixture_came_from_real_fetches_with_the_named_user_agent(self):
        self.assertRegex(self.data["user_agent"], r"^info-feed-seeds/\d+\.\d+\.\d+ \(\+https://github\.com/flywifi/info-feed-seeds\)$")
        self.assertEqual(self.data["window_days"], 30)
        hosts = {f["fetch"]["url"].split("/")[2] for f in self.data["feeds"]}
        self.assertEqual(hosts, {"www.nist.gov", "www.govinfo.gov"})
        responded = [f for f in self.data["feeds"] if f["fetch"]["error"] is None]
        self.assertGreater(len(responded), 0, "at least one feed responded when the fixture was built")
        for f in self.data["feeds"]:
            self.assertRegex(f["fetch"]["retrieved_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertIn(f["fetch"]["source_label"], ("live", "cache", "archive"))
            self.assertNotEqual(f["fetch"]["source_label"], "archive", "the fixture holds live titles only")

    def test_titles_fall_inside_the_window_or_carry_no_date(self):
        built = datetime.strptime(self.data["built_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        cutoff = built - timedelta(days=self.data["window_days"])
        self.assertGreater(len(self.data["items"]), 0)
        for item in self.data["items"]:
            self.assertTrue(item["title"], "every kept item has a title")
            if item["published"] is not None:
                when = datetime.strptime(item["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                self.assertGreaterEqual(when, cutoff)

    def test_guard_rescoring_matches_recorded_counts(self):
        counts = {guard.CLEAN: 0, guard.REVIEW: 0, guard.QUARANTINE: 0, guard.BLOCK: 0}
        for item in self.data["items"]:
            rec = guard.scan_text(item["title"])
            counts[rec["risk_level"]] += 1
            self.assertEqual(rec["risk_level"], item["guard_level"], item["title"])
            self.assertEqual(rec["total_score"], item["guard_score"], item["title"])
        self.assertEqual(counts, self.data["guard"]["counts"])
        total = sum(counts.values())
        held = counts[guard.QUARANTINE] + counts[guard.BLOCK]
        self.assertLessEqual(held / total, 0.05, "more than 5 percent of real government titles held: the patterns need review")


if __name__ == "__main__":
    unittest.main()
