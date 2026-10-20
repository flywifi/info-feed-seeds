import re
import unittest

import feeder
from tests.helpers import ROOT


class VersionTests(unittest.TestCase):
    def test_version_file_is_semver_and_matches_package(self):
        text = (ROOT / "VERSION").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"), "VERSION ends with a single LF")
        self.assertRegex(text.strip(), r"^\d+\.\d+\.\d+$")
        self.assertEqual(feeder.__version__, text.strip())
        self.assertEqual(feeder.__version__, "0.1.0")

    def test_user_agent_string(self):
        self.assertEqual(feeder.USER_AGENT,
                         "info-feed-seeds/0.1.0 (+https://github.com/flywifi/info-feed-seeds)")
        self.assertRegex(feeder.USER_AGENT,
                         r"^info-feed-seeds/\d+\.\d+\.\d+ \(\+https://github\.com/flywifi/info-feed-seeds\)$")
        self.assertTrue(feeder.USER_AGENT.isascii())

    def test_fetchkit_uses_the_package_user_agent(self):
        from feeder import fetchkit
        fetcher = fetchkit.Fetcher(state_dir=None)
        self.assertEqual(fetcher.user_agent, feeder.USER_AGENT)
        self.assertEqual(fetcher._headers("https://example.invalid/", False, None)["User-Agent"], feeder.USER_AGENT)


if __name__ == "__main__":
    unittest.main()
