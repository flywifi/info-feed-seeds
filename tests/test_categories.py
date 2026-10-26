"""The category registry: its shape and counts, its self-checks, the placement suggester and the
add command's rules."""
import copy
import tempfile
import unittest
from pathlib import Path

from feeder import categories, jsonio, topics
from feeder.schema_validate import validate
from tests.helpers import ROOT

REG = categories.load_categories(ROOT / "schema" / "categories.json")
SCHEMA = jsonio.load_json(ROOT / "schema" / "categories.schema.json")
TAGS = topics.all_tags()


class RegistryTests(unittest.TestCase):
    def test_registry_validates_and_is_canonical(self):
        self.assertEqual(validate(REG, SCHEMA), [])
        self.assertTrue(jsonio.is_canonical(ROOT / "schema" / "categories.json"))
        self.assertEqual(categories.registry_problems(REG, TAGS), [])
        self.assertEqual(categories.duplicate_keys(ROOT / "schema" / "categories.json"), [])

    def test_nine_categories_and_forty_six_subcategories_in_order(self):
        self.assertEqual(len(REG["categories"]), 9)
        self.assertEqual(sum(len(c["subcategories"]) for c in REG["categories"].values()), 46)
        self.assertEqual([cid for cid, _ in categories.ordered(REG)],
                         ["regulatory", "standards", "vulnerabilities", "attacks", "releases", "technology",
                          "guidance", "machine-data", "ai"])
        self.assertEqual(len(categories.all_refs(REG)), 9 + 46)

    def test_only_machine_data_is_off_by_default(self):
        off = [cid for cid, c in REG["categories"].items() if not c["digest_default"]]
        self.assertEqual(off, ["machine-data"])
        self.assertNotIn("machine-data", categories.default_on(REG))
        self.assertTrue(categories.digest_default(REG, "attacks"))
        self.assertFalse(categories.digest_default(REG, "machine-data"))
        self.assertTrue(categories.digest_default(REG, "not-a-category"), "an unknown category is not hidden")

    def test_pairs_and_industries(self):
        self.assertTrue(categories.is_valid_pair(REG, "attacks", "breaches"))
        self.assertFalse(categories.is_valid_pair(REG, "attacks", "science"))
        self.assertFalse(categories.is_valid_pair(REG, "nope", "breaches"))
        self.assertIn("cybersecurity", categories.industry_ids(REG))

    def test_registry_problems_name_each_defect(self):
        bad = copy.deepcopy(REG)
        bad["categories"]["ai"]["order"] = 1
        bad["categories"]["guidance"]["subcategories"] = {}
        bad["categories"]["attacks"]["subcategories"]["breaches"]["topics"] = ["not-a-topic"]
        bad["categories"]["Bad_Id"] = copy.deepcopy(REG["categories"]["guidance"]) | {"order": 50}
        bad["categories"]["releases"]["subcategories"]["developer"]["order"] = 1
        found = categories.registry_problems(bad, TAGS)
        self.assertIn("categories 'ai' and 'regulatory' share order 1", found)
        self.assertIn("category 'guidance' has no subcategories", found)
        self.assertIn("subcategory attacks/breaches: topic hint 'not-a-topic' is not in schema/topics.json", found)
        self.assertIn("category id 'Bad_Id' is not lowercase kebab", found)
        self.assertIn("subcategories releases/developer and releases/firmware share order 1", found)

    def test_duplicate_keys_are_found(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text('{"categories": {"ai": {"order": 1}, "ai": {"order": 2}}}\n', encoding="utf-8")
            self.assertEqual(categories.duplicate_keys(path), ["ai"])


class SuggestTests(unittest.TestCase):
    def test_version_number_titles_are_machine_data(self):
        found = categories.suggest(REG, ["37.319", "37.318", "37.317", "Weekly summary"], ["cybersecurity"])
        self.assertEqual(found, [{"ref": "machine-data/signature-versions", "score": 3,
                                  "reasons": ["3 of 4 titles are version numbers"]}])

    def test_record_id_titles_are_bulk_data(self):
        found = categories.suggest(REG, ["FR-2022-04-07", "PAI-2025-GSA"], ["government/federal"])
        self.assertEqual(found[0]["ref"], "machine-data/bulk-data")

    def test_topics_and_keywords_rank_reader_categories(self):
        titles = ["Federal Register Vol. 91, No. 120", "Proposed rule on cloud reporting", "Final rule on records"]
        found = categories.suggest(REG, titles, ["regulation/federal-rulemaking"])
        self.assertEqual(found[0]["ref"], "regulatory/federal-rules")
        self.assertEqual(found[0]["score"], 2 + 3)
        self.assertIn("topic regulation/federal-rulemaking", found[0]["reasons"])

    def test_keywords_match_whole_words_and_nothing_fits_is_empty(self):
        self.assertEqual(categories.suggest(REG, ["Breachless audit", "Quarterly note"], []), [])
        found = categories.suggest(REG, ["Data breach at a retailer"], [])
        self.assertEqual(found[0]["ref"], "attacks/breaches")
        self.assertEqual(categories.suggest(REG, [], []), [])


class ContentCategoryTests(unittest.TestCase):
    """An item's content category and content subcategory are its own; the source's stay in the manifest."""

    def place(self, title, summary="", source=("vulnerabilities", "vendor-advisories")):
        return categories.content_category(REG, title, summary, *source)

    def test_an_item_can_differ_from_its_source_at_both_levels(self):
        self.assertEqual(self.place("Ransomware campaign hits hospitals"),
                         {"category": "attacks", "subcategory": "threat-research", "category_basis": "item",
                          "subcategory_basis": "item", "reasons": ["title has 'campaign'"]})
        placed = self.place("PHP core RCE vulnerability patches", "actively exploited in the wild")
        self.assertEqual((placed["category"], placed["subcategory"]), ("attacks", "exploited-now"))

    def test_the_subcategory_is_decided_inside_the_category(self):
        placed = self.place("Data breach at a retailer", source=("attacks", "exploited-now"))
        self.assertEqual((placed["category"], placed["subcategory"], placed["subcategory_basis"]),
                         ("attacks", "breaches", "item"), "same category as the source, its own subcategory")
        weak = self.place("Note", "a phishing wave and a data leak", source=("attacks", "exploited-now"))
        self.assertEqual((weak["category"], weak["category_basis"], weak["subcategory"], weak["subcategory_basis"]),
                         ("attacks", "item", "exploited-now", "source"), "the text settles the category but not the subcategory")

    def test_no_signal_falls_back_to_the_source_at_both_levels(self):
        self.assertEqual(self.place("Quarterly update"),
                         {"category": "vulnerabilities", "subcategory": "vendor-advisories", "category_basis": "source",
                          "subcategory_basis": "source",
                          "reasons": ["no signal in the item text; the source category and source subcategory"]})
        one_hit = self.place("Note", "about a webinar")
        self.assertEqual(one_hit["category_basis"], "source", "a single summary hit is not enough")

    def test_title_shapes_decide_both_levels(self):
        self.assertEqual(self.place("37.319", source=("vulnerabilities", "detections"))["subcategory"], "signature-versions")
        self.assertEqual(self.place("FR-2022-04-07", source=("regulatory", "federal-rules"))["category"], "machine-data")

    def test_a_tie_keeps_the_source(self):
        placed = self.place("Advisory on a phishing campaign", source=("attacks", "scams"))
        self.assertEqual((placed["category"], placed["subcategory"]), ("attacks", "scams"))


class AddEntryTests(unittest.TestCase):
    def test_add_a_subcategory_to_an_existing_category(self):
        new = categories.add_entry(REG, "technology/robotics", label="Robotics", note="robots and automation",
                                   today="2026-10-26", keywords=["robot", " ", "automation"])
        sub = new["categories"]["technology"]["subcategories"]["robotics"]
        self.assertEqual(sub, {"label": "Robotics", "order": 9, "note": "robots and automation",
                               "keywords": ["robot", "automation"], "topics": []})
        self.assertNotIn("robotics", REG["categories"]["technology"]["subcategories"], "the input is not changed")
        self.assertEqual(validate(new, SCHEMA) + categories.registry_problems(new, TAGS), [])

    def test_a_new_category_needs_its_label_purpose_and_reason(self):
        with self.assertRaisesRegex(ValueError, "is new"):
            categories.add_entry(REG, "health/outbreaks", label="Outbreaks", note="n", today="2026-10-26")
        new = categories.add_entry(REG, "health/outbreaks", label="Outbreaks", note="disease outbreaks", today="2026-10-26",
                                   category_label="Health", purpose="what changed in public health", reason="health feeds",
                                   machine_data=False)
        self.assertEqual((new["categories"]["health"]["order"], new["categories"]["health"]["digest_default"]), (10, True))
        machine = categories.add_entry(REG, "telemetry/counters", label="Counters", note="n", today="2026-10-26",
                                       category_label="Telemetry", purpose="p", reason="r", machine_data=True)
        self.assertFalse(machine["categories"]["telemetry"]["digest_default"])

    def test_refusals(self):
        for ref, kw, message in (("technology", {}, "category/subcategory"),
                                 ("technology/Bad", {}, "lowercase kebab"),
                                 ("technology/science", {}, "already in the registry"),
                                 ("technology/robotics", {"machine_data": True}, "apply only to a new one")):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, message):
                categories.add_entry(REG, ref, label="L", note="n", today="2026-10-26", **kw)


if __name__ == "__main__":
    unittest.main()
