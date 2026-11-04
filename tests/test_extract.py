"""Tag readers on planted item text: CVE ids, severity, exploitation, vendors and deadlines, the vendor
registry's own checks, and the readers' time on large inputs."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from feeder import extract, jsonio
from tests.helpers import ROOT

NAMES = extract.load_vendors(ROOT / "schema" / "vendors.json")


def elapsed(fn, *args):
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


class CveTests(unittest.TestCase):
    def test_ids_are_upper_case_once_each_in_order(self):
        self.assertEqual(extract.cve_ids("cve-2026-1234 and CVE-2026-109618, then CVE-2026-1234 again"),
                         ["CVE-2026-1234", "CVE-2026-109618"])

    def test_malformed_ids_are_not_ids(self):
        for text in ("CVE-26-1", "CVE-2026-123", "CVE-2024-00001", "CVE-\u0662\u0660\u0662\u0666-1234", "CVE-\uff12\uff10\uff12\uff16-1234"):
            with self.subTest(text=text):
                self.assertEqual(extract.cve_ids(text), [])

    def test_many_distinct_ids_take_linear_time(self):
        text = " ".join(f"CVE-2026-{n:05d}" for n in range(10000, 19000))
        self.assertGreaterEqual(len(text), 100000)
        self.assertLess(elapsed(extract.cve_ids, text), 0.25)


class SeverityTests(unittest.TestCase):
    def test_words_in_a_severity_context(self):
        self.assertEqual(extract.severity("Patch released (High)"), "high")
        self.assertEqual(extract.severity("a critical-severity flaw"), "critical")
        self.assertEqual(extract.severity("Severity: medium"), "medium")
        self.assertEqual(extract.severity("rated low by the vendor"), "low")

    def test_a_cvss_score_sets_the_band_and_the_highest_wins(self):
        self.assertEqual(extract.severity("(Medium) CVSS v3.1 base score: 9.8"), "critical")
        self.assertEqual(extract.severity("CVSS 7.5"), "high")
        self.assertEqual(extract.severity("CVSS 9.8."), "critical")
        self.assertEqual(extract.severity("CVSS score of 4.0"), "medium")
        self.assertEqual(extract.severity("A CVSS v3.1 base score of 3.1 has been calculated; vector CVSS:4.0/AV:L/AC:H"), "low")
        for score, word in ((0.0, "none"), (3.9, "low"), (4.0, "medium"), (6.9, "medium"), (8.9, "high"), (10.0, "critical")):
            self.assertEqual(extract.severity_from_score(score), word)
        self.assertIsNone(extract.severity_from_score(10.1))

    def test_a_cvss_version_or_vector_is_not_a_score(self):
        for text in ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H",
                     "vector (CVSS4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H)", "FIRST publishes CVSS 4.0 specification",
                     "NVD now scores with CVSS 3.1", "CVSS v2 base score 9.3"):
            with self.subTest(text=text):
                self.assertIsNone(extract.severity(text))

    def test_a_colon_written_version_before_a_score(self):
        cases = {"This vulnerability has a CVSS: 3.1 9.8 / 8.5": "critical", "CVSS:3.1 9.8 / 8.5": "critical",
                 "CVSS:3.1 Base Score: 9.8": "critical", "CVSS:4.0 9.3": "critical", "CVSS:3.1": None, "CVSS: 2.0 7.5": None,
                 "CVSS: 4.0": "medium", "CVSS: 7.5": "high", "CVSS:3.1/AV:N/AC:L": None, "CVSS: 3.1/AV:N": None}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract.severity(text), expected)

    def test_ordinary_words_are_not_a_severity(self):
        for text in ("critical infrastructure operators", "high availability clusters", "a low-cost device", ""):
            self.assertIsNone(extract.severity(text), text)

    def test_long_whitespace_runs_take_linear_time(self):
        for text in ("severity" + " " * 100000 + "x", "CVSS" + " " * 100000 + "x"):
            with self.subTest(text=text[:8]):
                self.assertLess(elapsed(extract.severity, text), 0.25)


class ExploitedTests(unittest.TestCase):
    def test_exploitation_phrases(self):
        for text in ("actively exploited", "Exploited in the wild", "Active Zero-Day: CVE-2026-1", "exploitation has been observed",
                     "FortiGuard Labs is monitoring active exploitation of multiple zero-day vulnerabilities",
                     "Google is aware that an exploit for CVE-2026-1234 exists in the wild.",
                     "Apple is aware of a report that this issue may have been exploited",
                     "Microsoft has detected exploitation of this vulnerability", "exploited as a zero-day"):
            with self.subTest(text=text):
                self.assertTrue(extract.exploited(text))

    def test_a_negation_about_something_else_does_not_switch_exploitation_off(self):
        for text in ("CVE-2026-1234, which requires no user interaction, is actively exploited",
                     "The vulnerability can be exploited remotely without authentication and has been actively exploited in the wild",
                     "The bug, which can be triggered without user interaction, is being actively exploited in the wild",
                     "No patch is available, and the flaw is actively exploited",
                     "The flaw is not only easy to trigger but also actively exploited",
                     "Attackers no longer need credentials, and the bug is actively exploited"):
            with self.subTest(text=text):
                self.assertTrue(extract.exploited(text))
        self.assertTrue(extract.tags("No fix yet for Ivanti VPN flaw", "Ivanti says Connect Secure is actively exploited", NAMES)["exploited"],
                        "a negation in the title does not reach the summary")
        for text in ("This is not a drill. The flaw is actively exploited.", "Not every customer is affected; the bug is actively exploited",
                     "Updates are not automatic, but the flaw is actively exploited"):
            with self.subTest(text=text):
                self.assertTrue(extract.exploited(text), "a negation in an earlier clause does not reach the phrase")
        self.assertTrue(extract.tags("Patch not yet available", "The flaw is actively exploited", NAMES)["exploited"])

    def test_negated_or_hedged_exploitation_is_not_exploitation(self):
        for text in ("a proof of concept was published", "has not been exploited in the wild",
                     "is not aware of this vulnerability being exploited in the wild",
                     "There is no evidence it was exploited in the wild", "no exploitation has been observed",
                     "could be actively exploited", "not listed in the Known Exploited Vulnerabilities catalog",
                     "Microsoft is not aware of active exploitation", "may be actively exploited",
                     "would be actively exploited if left unpatched"):
            with self.subTest(text=text):
                self.assertFalse(extract.exploited(text))


class VendorTests(unittest.TestCase):
    def test_whole_words_with_their_exact_capitalisation(self):
        self.assertEqual(extract.vendors("FortiOS and FortiGate fixes", NAMES), ["fortinet"])
        self.assertEqual(extract.vendors("Cisco IOS XE and Apple iOS", NAMES), ["apple", "cisco"])
        self.assertEqual(extract.vendors("ios xe in lower case and fortinetwork", NAMES), [])

    def test_a_research_team_reporting_on_others_is_not_their_vendor(self):
        self.assertEqual(extract.vendors("What is the Attack? FortiGuard Labs is monitoring CVE-2026-98432 in the Linux kernel",
                                         NAMES), [])

    def test_vendor_matching_takes_linear_time(self):
        self.assertLess(elapsed(extract.vendors, "word " * 20000, NAMES), 0.25)

    def test_registry_checks(self):
        self.assertEqual(extract.vendor_problems(ROOT / "schema" / "vendors.json"), [])
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "vendors.json"
            jsonio.dump_json(bad, {"vendors": {
                "Bad Id": {"aliases": ["Xx"]}, "trailing\n": {"aliases": ["Yy"]}, "a": {"aliases": []},
                "b": {"aliases": ["Shared"]}, "c": {"aliases": ["Shared"]}, "d": "not an object",
                "e": {"aliases": "Letters"}, "f": {"aliases": ["Ok", 7]}, "g": {"aliases": ["Twice", "Twice"]},
                "h": {"aliases": [" Padded"]}, "i": {"aliases": ["Q"]}}})
            problems = extract.vendor_problems(bad)
            self.assertEqual(len(problems), 10, problems)
            self.assertEqual(sorted(extract.load_vendors(bad)), ["Bad Id", "b", "c", "f", "g", "trailing\n"],
                             "the poll reads only usable entries and never crashes")
            jsonio.dump_json(bad, {"vendors": {"acme": {"aliases": ["Acme"]}, "beta": {"aliases": ["Acme Cloud"]}}})
            self.assertEqual(extract.vendor_problems(bad), ["alias 'Acme' (acme) is inside alias 'Acme Cloud' (beta) and is hidden by it"])
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(len(extract.vendor_problems(bad)), 1)
            self.assertEqual(extract.load_vendors(bad), {})
            bad.write_text(json.dumps({"vendors": []}), encoding="utf-8")
            self.assertEqual(len(extract.vendor_problems(bad)), 1)


class DeadlineTests(unittest.TestCase):
    def test_dates_tied_to_a_deadline(self):
        text = ("Comments are due March 31, 2027. Effective date: 1 July 2027. The deadline 2027-01-15 stands. "
                "Published October 1, 2026.")
        self.assertEqual(extract.deadlines(text), ["2027-03-31", "2027-07-01", "2027-01-15"])

    def test_common_deadline_wording(self):
        cases = {
            "Comments must be received on or before December 5, 2026.": ["2026-12-05"],
            "Submit comments by December 5, 2026.": ["2026-12-05"],
            "Deadline: Nov. 10, 2026.": ["2026-11-10"],
            "Deadline: Nov 10, 2026": ["2026-11-10"],
            "Deadline: March 31st, 2027": ["2027-03-31"],
            "Deadline: 1st July 2027": ["2027-07-01"],
            "The rule takes effect January 1, 2027.": ["2027-01-01"],
            "Agencies are required to remediate by November 10, 2026.": ["2026-11-10"],
            "deadline for version 2, 2027 builds is March 31, 2027": ["2027-03-31"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract.deadlines(text), expected)

    def test_deadlines_with_a_time_a_weekday_or_an_order(self):
        cases = {
            "Comments must be received on or before 11:59 p.m. Eastern Time on December 5, 2026.": ["2026-12-05"],
            "Comments must be received on or before Monday, January 5, 2027.": ["2027-01-05"],
            "Deadline: Friday, December 5, 2026": ["2026-12-05"],
            "Federal Civilian Executive Branch agencies must remediate by November 24, 2026.": ["2026-11-24"],
            "CISA requires federal agencies to apply mitigations by November 24, 2026.": ["2026-11-24"],
            "The effective date of this rule is January 1, 2027.": ["2027-01-01"],
            "Due date 2026-11-24T00:00:00Z": ["2026-11-24"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract.deadlines(text), expected)

    def test_a_publication_date_is_not_the_deadline(self):
        for text in ("The rule takes effect 30 days after publication on November 4, 2026.",
                     "The rule takes effect 60 days following its publication on November 4, 2026."):
            with self.subTest(text=text):
                self.assertEqual(extract.deadlines(text), [])

    def test_dates_that_are_not_deadlines(self):
        for text in ("A cost-effective fix released March 3, 2026.", "highly effective since 1 June 2026.",
                     "Report title. Effective immediately\nPublished October 1, 2026"):
            with self.subTest(text=text):
                self.assertEqual(extract.deadlines(text), [])
        self.assertIn(extract.deadlines("The deadline, announced on October 1, 2026, is January 15, 2027."), ([], ["2027-01-15"]))

    def test_impossible_or_foreign_dates_are_not_guessed(self):
        self.assertEqual(extract.deadlines("Deadline: February 30, 2027. Due date 1 Juli 2027. Deadline 31 Mars 2027. "
                                           "Due date 2027-02-30."), [])

    def test_long_text_takes_linear_time(self):
        self.assertLess(elapsed(extract.deadlines, "deadline " * 12000), 0.25)


class TagsTests(unittest.TestCase):
    def test_one_item(self):
        got = extract.tags("FortiOS heap overflow (Critical)", "CVE-2026-24858 is actively exploited. Patches must be "
                           "applied by November 10, 2026.", NAMES)
        self.assertEqual(got, {"cves": ["CVE-2026-24858"], "severity": "critical", "severity_rank": 4, "exploited": True,
                               "vendors": ["fortinet"], "deadline": "2026-11-10"})
        self.assertEqual(extract.tags("Workshop announced", "", NAMES),
                         {"cves": [], "severity": None, "severity_rank": None, "exploited": False, "vendors": [], "deadline": None})


if __name__ == "__main__":
    unittest.main()
