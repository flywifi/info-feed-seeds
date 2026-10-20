"""The repository schemas validate their fixtures, and the mutations that must fail do fail."""
import copy
import unittest

from feeder import jsonio
from feeder.schema_validate import validate
from tests.helpers import FIXTURES, ROOT

SCHEMA_DIR = ROOT / "schema"


def load_schema(name):
    return jsonio.load_json(SCHEMA_DIR / name)


def load_fixture(name):
    return jsonio.load_json(FIXTURES / "schema" / name)


class SchemaFixtureTests(unittest.TestCase):
    def test_schema_files_are_canonical_json(self):
        for path in sorted(SCHEMA_DIR.glob("*.json")):
            self.assertTrue(jsonio.is_canonical(path), f"{path.name} is not in canonical form (run the writer)")

    def test_source_example_validates(self):
        self.assertEqual(validate(load_fixture("source_example.json"), load_schema("source.schema.json")), [])

    def test_source_mutations_fail(self):
        schema = load_schema("source.schema.json")
        base = load_fixture("source_example.json")

        bad_id = copy.deepcopy(base)
        bad_id["id"] = "Not Valid"
        self.assertTrue(validate(bad_id, schema))

        unknown = copy.deepcopy(base)
        unknown["notes"] = "private"
        self.assertTrue(validate(unknown, schema), "additional properties are refused")

        bad_route = copy.deepcopy(base)
        bad_route["routes"][0]["kind"] = "scrape"
        self.assertTrue(validate(bad_route, schema))

        no_routes = copy.deepcopy(base)
        no_routes["routes"] = []
        self.assertTrue(validate(no_routes, schema))

        bad_topic = copy.deepcopy(base)
        bad_topic["topics"] = ["Cyber Security"]
        self.assertTrue(validate(bad_topic, schema))

        auth_without_local = copy.deepcopy(base)
        auth_without_local["auth"] = {"credential": "govinfo-api-key"}
        self.assertTrue(validate(auth_without_local, schema), "auth requires runs_on local only")
        auth_without_local["runs_on"] = ["local"]
        self.assertEqual(validate(auth_without_local, schema), [])

        bad_credential = copy.deepcopy(auth_without_local)
        bad_credential["auth"] = {"credential": "Matt's key"}
        self.assertTrue(validate(bad_credential, schema))

        bad_discovery = copy.deepcopy(base)
        bad_discovery["discovered_from"]["kind"] = "guess"
        self.assertTrue(validate(bad_discovery, schema))

        bad_review = copy.deepcopy(base)
        bad_review["classification_review"] = "done"
        self.assertTrue(validate(bad_review, schema))

    def test_methods_example_validates(self):
        self.assertEqual(validate(load_fixture("methods_example.json"), load_schema("methods.schema.json")), [])

    def test_methods_mutations_fail(self):
        schema = load_schema("methods.schema.json")
        base = load_fixture("methods_example.json")
        attempt = base["routes"][0]["attempts"][0]

        archive_without_capture = copy.deepcopy(base)
        archive_without_capture["routes"][0]["attempts"][0]["source_label"] = "archive"
        self.assertTrue(validate(archive_without_capture, schema), "archive needs a fetched capture_url")
        archive_without_capture["routes"][0]["attempts"][0]["capture_url"] = "https://web.archive.org/web/2/https://www.nist.gov/"
        self.assertEqual(validate(archive_without_capture, schema), [])

        bad_media = copy.deepcopy(base)
        bad_media["routes"][0]["attempts"][0]["media"] = [{"file": "grab.png", "sha256": "abc", "kind": "screenshot"}]
        self.assertTrue(validate(bad_media, schema))

        bad_time = copy.deepcopy(base)
        bad_time["routes"][0]["attempts"][0]["tried_at"] = "2026-10-20 12:00"
        self.assertTrue(validate(bad_time, schema))

        bad_result = copy.deepcopy(base)
        bad_result["routes"][0]["attempts"][0]["result"] = "maybe"
        self.assertTrue(validate(bad_result, schema))
        self.assertIn("source_label", attempt)

    def test_profile_example_validates(self):
        self.assertEqual(validate(load_fixture("profile_example.json"), load_schema("profile.schema.json")), [])
        bad = load_fixture("profile_example.json")
        bad["daily_cap"] = 0
        self.assertTrue(validate(bad, load_schema("profile.schema.json")))

    def test_item_example_validates(self):
        self.assertEqual(validate(load_fixture("item_example.json"), load_schema("item.schema.json")), [])
        bad = load_fixture("item_example.json")
        bad["published"] = "2026-10-20"
        self.assertTrue(validate(bad, load_schema("item.schema.json")), "published must be a full UTC Z timestamp")
        bad["published"] = None
        bad["display"]["summary"] = "x" * 301
        self.assertTrue(validate(bad, load_schema("item.schema.json")))

    def test_fixture_topics_exist_in_registry(self):
        from feeder.topics import all_tags
        tags = all_tags()
        for name in ("source_example.json", "profile_example.json", "item_example.json"):
            for tag in load_fixture(name).get("topics", []):
                self.assertIn(tag, tags, f"{name} uses an unregistered topic {tag}")


if __name__ == "__main__":
    unittest.main()
