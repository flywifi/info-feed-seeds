"""Every committed source folder validates against both schemas, and the id equals the folder name.
The committed profile and guard releases file validate too."""
import unittest

from feeder import adapters, categories, jsonio
from feeder.config import Repo, lint, release_problems
from feeder.schema_validate import validate
from feeder.topics import all_tags
from tests.helpers import ROOT

REPO = Repo(root=ROOT)


class SourceFolderTests(unittest.TestCase):
    def test_there_are_sources(self):
        self.assertGreater(len(REPO.source_folders()), 0)

    def test_every_folder_validates(self):
        source_schema = jsonio.load_json(ROOT / "schema" / "source.schema.json")
        methods_schema = jsonio.load_json(ROOT / "schema" / "methods.schema.json")
        tags = all_tags()
        for folder in REPO.source_folders():
            with self.subTest(source=folder.name):
                manifest = jsonio.load_json(folder / "source.json")
                methods = jsonio.load_json(folder / "methods.json")
                self.assertEqual(validate(manifest, source_schema), [])
                self.assertEqual(validate(methods, methods_schema), [])
                self.assertEqual(manifest["id"], folder.name)
                self.assertEqual(methods["id"], folder.name)
                for tag in manifest["topics"]:
                    self.assertIn(tag, tags)
                self.assertIsNotNone(manifest["verified"], "only proven sources are committed")
                self.assertEqual(manifest["classification_review"], "pending")
                polled = {r["url"] for r in manifest["routes"] if adapters.configured(r)}
                proven = [r for r in methods["routes"] if r["url"] in polled and r["last_success"]]
                self.assertTrue(proven, "a configured route has a recorded success")
                self.assertEqual(proven[0]["last_success"]["source_label"], "live")

    def test_every_source_is_placed_in_the_category_registry(self):
        reg = categories.load_categories(ROOT / "schema" / "categories.json")
        for folder in REPO.source_folders():
            with self.subTest(source=folder.name):
                manifest = jsonio.load_json(folder / "source.json")
                self.assertTrue(categories.is_valid_pair(reg, manifest["category"], manifest["subcategory"]))
                for industry in manifest.get("industries", []):
                    self.assertIn(industry, categories.industry_ids(reg))

    def test_machine_data_sources_are_stored_off_and_reader_sources_on(self):
        for sid, src in REPO.load_sources().items():
            if sid.startswith(("fortiguard-", "fortinet-")):
                with self.subTest(source=sid):
                    self.assertEqual(src.manifest["enabled"], src.digest_default)

    def test_lint_is_clean(self):
        self.assertEqual(lint(REPO), [])

    def test_profile_and_releases(self):
        profile = REPO.load_profile("technology-regulation")
        self.assertEqual(profile["daily_cap"], 80)
        self.assertEqual(release_problems(jsonio.load_json(ROOT / "guard" / "releases.json")), [])


if __name__ == "__main__":
    unittest.main()
