"""Tag readers on planted item text: CVE ids, severity, exploitation, vendors and deadlines, the vendor
registry's own checks, and the readers' time on large inputs."""
import itertools
import json
import tempfile
import time
import unittest
from pathlib import Path

from feeder import extract, items, jsonio
from tests.helpers import ROOT

NAMES = extract.load_vendors(ROOT / "schema" / "vendors.json")
STD = extract.load_standards(ROOT / "schema" / "standards.json")


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

    def test_vendor_scale_words_map_to_bands_in_a_severity_context(self):
        for text, want in (("Security update (Important)", "high"), ("Severity: Moderate", "medium"),
                           ("an important-severity fix", "high"), ("rated moderate by the vendor", "medium")):
            with self.subTest(text=text):
                self.assertEqual(extract.severity(text), want)
        for text in ("an important update for customers", "moderate traffic on the site"):
            with self.subTest(text=text):
                self.assertIsNone(extract.severity(text))

    def test_a_severity_word_after_not_or_no_in_the_same_clause_is_skipped(self):
        self.assertIsNone(extract.severity("The flaw is not rated critical"))
        self.assertIsNone(extract.severity("no high-severity issues were found"))
        self.assertEqual(extract.severity("Not affected. The second flaw is rated critical"), "critical",
                         "a negation in an earlier sentence does not reach the word")

    def test_a_negation_about_something_else_keeps_the_severity(self):
        for text in ("Remote code execution, no user interaction required (Critical)",
                     "The flaw, which does not require authentication, is rated critical",
                     "This is not only rated critical but actively exploited", "Advisory No 12 (Critical)",
                     "no-auth RCE in the web UI (Critical)", "No patch is available for this (Critical) flaw",
                     "No action needed, critical severity", "pia" + "no " + "z" * 37 + "(Critical)",
                     "Patched: no. Rated critical by the vendor"):
            with self.subTest(text=text):
                self.assertEqual(extract.severity(text), "critical")
        for text in ("has not been rated critical", "There are no critical-severity flaws", "no longer rated critical",
                     "This is not a critical-severity issue", "is not currently rated critical",
                     "not considered high severity"):
            with self.subTest(text=text):
                self.assertIsNone(extract.severity(text))

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
    def test_a_hyphen_after_a_name_still_names_the_vendor_but_one_before_does_not(self):
        self.assertEqual(extract.vendors("a Microsoft-signed driver", NAMES), ["microsoft"])
        self.assertEqual(extract.vendors("Cisco-branded phones", NAMES), ["cisco"])
        self.assertEqual(extract.vendors("non-Microsoft drivers", NAMES), [])
        self.assertEqual(extract.vendors("MICROSOFT and CISCO in capitals", NAMES), [],
                         "exact capitalisation keeps Cisco IOS and Apple iOS apart, so capitals are not read")

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


class StandardsTests(unittest.TestCase):
    def test_each_family_and_its_edition(self):
        for text, std, edition in (
                ("Guidance on SP 800-53 Rev. 5 controls", "nist-sp-800-53", "nist-sp-800-53:r5"),
                ("Mapping to SP 800-53r5", "nist-sp-800-53", "nist-sp-800-53:r5"),
                ("SP 800-171 Revision 3 is final", "nist-sp-800-171", "nist-sp-800-171:r3"),
                ("Validated under FIPS 140-3", "fips-140", "fips-140:3"),
                ("Certified to ISO/IEC 27001:2022", "iso-27001", "iso-27001:2022"),
                ("The ISO 27001:2013 transition ends", "iso-27001", "iso-27001:2013"),
                ("PCI DSS v4.0.1 is now in force", "pci-dss", "pci-dss:4.0.1"),
                ("CIS Controls v8 mapping", "cis-controls", "cis-controls:8"),
                ("CMMC 2.0 assessments begin", "cmmc", "cmmc:2.0"),
                ("Profiles for CSF 2.0", "nist-csf", "nist-csf:2.0"),
                ("The NIST Cybersecurity Framework 2.0 quick start", "nist-csf", "nist-csf:2.0"),
                ("NIST 800-53 Rev. 5 baselines", "nist-sp-800-53", "nist-sp-800-53:r5"),
                ("SP800-53r5 overlays", "nist-sp-800-53", "nist-sp-800-53:r5"),
                ("NIST 800-171 Rev. 3 assessments", "nist-sp-800-171", "nist-sp-800-171:r3"),
                ("PCI-DSS v4.0 deadlines", "pci-dss", "pci-dss:4.0"),
                ("ISO27001:2022 certificate", "iso-27001", "iso-27001:2022"),
                ("CIS Controls Version 8.1 guide", "cis-controls", "cis-controls:8.1"),
                ("NIST CSF 1.1 crosswalk", "nist-csf", "nist-csf:1.1")):
            with self.subTest(text=text):
                self.assertEqual(extract.standards(text, STD), [std])
                self.assertEqual(extract.editions(text, STD), [edition])
        for text, std in (("FedRAMP 20x Class C", "fedramp"), ("HIPAA Security Rule", "hipaa"), ("GDPR fines", "gdpr"),
                          ("NIS2 transposition", "nis2"), ("EU DORA reporting", "dora"),
                          ("the Digital Operational Resilience Act applies", "dora"), ("a SOC 2 Type II report", "soc-2"),
                          ("CIS Benchmarks for Linux", "cis-benchmarks"), ("SP 800-61 incident handling", "nist-sp-800-61"),
                          ("Zero trust in SP 800-207", "nist-sp-800-207"), ("NIST IR 8625 O-RAN security", "nist-ir-8625")):
            with self.subTest(text=text):
                self.assertEqual(extract.standards(text, STD), [std])
                self.assertEqual(extract.editions(text, STD), [], "no edition is written next to it")

    def test_names_that_are_not_a_standard(self):
        for text in ("Download the ISO image", "SOC analyst hiring", "the SP 800 series", "our framework 2.0 release",
                     "a soc 2 report in lower case", "SP 800-53A assessment procedures", "fedramp in lower case",
                     "PreFedRAMP drafts", "non-FedRAMP clouds", "Google 2025 DORA Report on DevOps",
                     "Acme Releases Cybersecurity Framework 2.0 for Clients"):
            with self.subTest(text=text):
                self.assertEqual((extract.standards(text, STD), extract.editions(text, STD)), ([], []))

    def test_control_numbers_and_counts_are_not_editions(self):
        for text in ("CIS Controls 1-6 first", "CIS Controls 3.4 safeguard", "CIS Controls 18 in total"):
            with self.subTest(text=text):
                self.assertEqual((extract.standards(text, STD), extract.editions(text, STD)), (["cis-controls"], []))
        got = extract.tags("Updates to CMMC", "2.1 million contractors affected", NAMES, STD)
        self.assertEqual((got["standards"], got["editions"]), (["cmmc"], []), "a version is never read across lines")
        self.assertEqual(extract.editions("SP 800-53 r5 and SP 800-53, Rev. 5", STD), [], "only a revision written right after")

    def test_an_alias_with_its_own_edition_needs_a_whole_version(self):
        self.assertEqual((extract.standards("CSF 2.0.1 notes", STD), extract.editions("CSF 2.0.1 notes", STD)),
                         (["nist-csf"], []))
        self.assertEqual(extract.editions("CSF 2.0. Then more", STD), ["nist-csf:2.0"], "a sentence end is no version part")

    def test_at_most_twenty_editions_are_kept_for_an_item(self):
        text = " ".join(f"PCI DSS 1.{n}" for n in range(40))
        self.assertEqual(len(extract.editions(text, STD)), 40)
        got = extract.tags(text, "", NAMES, STD)["editions"]
        self.assertEqual(got, [f"pci-dss:1.{n}" for n in range(20)])

    def test_first_mention_order_each_once(self):
        text = "FedRAMP and HIPAA, then FedRAMP again and SP 800-53 Rev. 5 and SP 800-53 Rev. 5"
        self.assertEqual(extract.standards(text, STD), ["fedramp", "hipaa", "nist-sp-800-53"])
        self.assertEqual(extract.editions(text, STD), ["nist-sp-800-53:r5"])

    def test_an_edition_belongs_only_to_the_name_it_follows(self):
        self.assertEqual(extract.standards("SP 800-53 and FIPS 140-3", STD), ["nist-sp-800-53", "fips-140"])
        self.assertEqual(extract.editions("SP 800-53 and FIPS 140-3", STD), ["fips-140:3"])
        self.assertEqual(extract.editions("FIPS 140-23 and PCI DSS v4.0.1.2", STD), [], "an edition is read whole or not at all")
        self.assertEqual(extract.editions("FIPS 140-3-1 and SP 800-53 Rev. 5.1", STD), [])
        self.assertEqual(extract.editions("HIPAA 2.0 and CMMC rules version 2.0", STD), [],
                         "a version after another name, or not right after its own, is no edition")

    def test_sanitized_text_is_read(self):
        got = extract.tags(items.sanitize("Guide to SP&nbsp;800-53 Rev.&nbsp;5"), "", NAMES, STD)
        self.assertEqual((got["standards"], got["editions"]), (["nist-sp-800-53"], ["nist-sp-800-53:r5"]))

    def test_the_readers_take_linear_time(self):
        distinct = " ".join(f"PCI DSS {a}.{b}.{c}" for a, b, c in
                            itertools.islice(itertools.product(range(1, 100), range(100), range(100)), 8000))
        for text in ("SP 800-53 Rev. 5 " * 6000, "FedRAMP " * 12500, "S" * 100_000, "CSF 2.0 " * 12500, distinct):
            for reader in (extract.standards, extract.editions):
                with self.subTest(reader=reader.__name__, text=text[:10]):
                    start = time.perf_counter()
                    reader(text[:100_000], STD)
                    self.assertLess(time.perf_counter() - start, 0.1)

    def test_the_registry_lints_clean_and_reports_each_defect(self):
        self.assertEqual(extract.standard_problems(ROOT / "schema" / "standards.json"), [])
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "standards.json"
            jsonio.dump_json(bad, {"standards": {"Bad Id": {"aliases": ["Xx"]}, "a": {"aliases": []}, "b": {"aliases": ["Q"]},
                                                 "c": {"aliases": ["Shared"]}, "d": {"aliases": ["Shared"]}}})
            problems = extract.standard_problems(bad)
            for want in ("standard id 'Bad Id' is not lower-case words joined by hyphens", "standard 'a' has no aliases",
                         "standard 'b': alias 'Q' is too short", "alias 'Shared' is claimed by 'c' and 'd'"):
                self.assertIn(want, problems)
            jsonio.dump_json(bad, {"standards": {"iso": {"aliases": ["ISO 27001"]}, "x": {"aliases": ["ISO 27001 Annex"]}}})
            self.assertEqual(extract.standard_problems(bad),
                             ["alias 'ISO 27001' (iso) is inside alias 'ISO 27001 Annex' (x) and is hidden by it"])
            jsonio.dump_json(bad, {"standards": {"a": {"aliases": ["FIPS 140"]}, "b": {"aliases": ["FIPS 140-3"]}}})
            self.assertEqual(extract.standard_problems(bad),
                             ["alias 'FIPS 140' (a) is inside alias 'FIPS 140-3' (b) and is hidden by it"],
                             "the lint uses the matcher's own right edge")
            jsonio.dump_json(bad, {"standards": {"a": {"aliases": ["SP 800-53"]}, "b": {"aliases": ["SP 800-53r5"]}}})
            self.assertEqual(extract.standard_problems(bad),
                             ["alias 'SP 800-53' (a) is inside alias 'SP 800-53r5' (b) and is hidden by it"],
                             "a revision written straight after a name is within the matcher's edge")


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
                               "vendors": ["fortinet"], "deadline": "2026-11-10", "standards": [], "editions": []})
        self.assertEqual(extract.tags("Workshop announced", "", NAMES),
                         {"cves": [], "severity": None, "severity_rank": None, "exploited": False, "vendors": [], "deadline": None,
                          "standards": [], "editions": []})


if __name__ == "__main__":
    unittest.main()
