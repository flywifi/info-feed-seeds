"""Repository hygiene: no em dashes in tracked text, standard library only in feeder/ and tools/,
every docs page carries the review header, and the ignore list has the required entries."""
import ast
import sys
import unittest
from pathlib import Path

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import emdash_scan  # noqa: E402


class EmDashTests(unittest.TestCase):
    def test_no_em_dash_in_tracked_text(self):
        hits = emdash_scan.scan(ROOT)
        self.assertEqual(hits, [], "em dashes found: " + ", ".join(f"{p}:{l}:{c}" for p, l, c in hits))

    def test_scanner_finds_one_when_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.md"
            p.write_text("a \u2014 b\nc\n", encoding="utf-8")
            self.assertEqual(emdash_scan.scan(Path(td), files=[p]), [("x.md", 1, 3)])


class StdlibOnlyTests(unittest.TestCase):
    def test_feeder_and_tools_import_only_the_standard_library(self):
        allowed_local = {"feeder", "tests", "emdash_scan"}
        offenders = []
        for folder in ("feeder", "tools"):
            for path in (ROOT / folder).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        names = [node.module]
                    for name in names:
                        top = name.split(".")[0]
                        if top in allowed_local or top in sys.stdlib_module_names:
                            continue
                        if top == "crawl":
                            offenders.append(f"{path.relative_to(ROOT)}: imports crawl")
                        else:
                            offenders.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(offenders, [])


class LayoutTests(unittest.TestCase):
    def test_gitignore_entries(self):
        lines = {l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
        for required in (".cache/", "local/", "logs/", ".venv-crawl/", "*.local.json", ".secrets.local.json"):
            self.assertIn(required, lines)

    def test_gitattributes(self):
        self.assertEqual((ROOT / ".gitattributes").read_text(encoding="utf-8").strip(), "* text=auto eol=lf")

    def test_docs_carry_review_header(self):
        for path in (ROOT / "docs").rglob("*.md"):
            first = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertRegex(first, r"^<!-- last_reviewed: \d{4}-\d{2}-\d{2} \| owner: [a-z-]+ -->$", str(path))

    def test_changelog_has_unreleased_section(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [Unreleased]", text)
        self.assertIn("keepachangelog.com", text)

    def test_tracked_text_files_are_lf_and_utf8(self):
        for path in emdash_scan.tracked_files(ROOT):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if b"\x00" in data[:8192]:
                continue
            self.assertNotIn(b"\r\n", data, f"{path} has CRLF line endings")
            data.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
