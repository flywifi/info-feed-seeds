"""One rulebook for every coding tool. AGENTS.md at the root carries every standing rule, within the
size and line count the tools that read it accept, imports nothing and names no AI product, so a new
product or model never needs an edit to it. CLAUDE.md is exactly an import of it. A pointer file is
accepted under two names only (CLAUDE.md and GEMINI.md at the root): one reader takes the first
instruction file it finds, in an order that puts several other names before AGENTS.md, so a pointer
under one of those names would hide the rulebook from it. Every other instruction file a tool might
read, at any depth, is absent, and the root holds no Markdown besides the files named here. A tool's
settings file that names the rulebook must keep naming it."""
import json
import re
import sys
import unittest

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import commit_check as cc  # noqa: E402
import emdash_scan  # noqa: E402

RULEBOOK = ROOT / "AGENTS.md"
POINTER = b"@AGENTS.md\n"
MAX_BYTES = 16 * 1024  # one reader takes only the first 16 KiB; others cap a file at 20,000 or 24,000 bytes
MAX_LINES = 200  # readers warn on an instruction file over 200 lines
POINTER_MAX_BYTES = 400
POINTER_NAMES = {"CLAUDE.md", "GEMINI.md"}
ROOT_MARKDOWN = {"README.md", "CHANGELOG.md", "AGENTS.md"} | POINTER_NAMES
HEADINGS = ("## How these rules are read", "## Repo lock", "## Tool defaults to switch off", "## Branches",
            "## Content rules", "## Code rules", "## Reporting")
# Settings files that point a tool at the rulebook: path, then the keys down to the list of file names.
SETTINGS_NAMING_THE_RULEBOOK = {".gemini/settings.json": ("context", "fileName")}
# Files and folders coding tools read instructions from, at any depth.
INSTRUCTION_PATHS = re.compile(
    r"(?:^|/)(?:AGENTS?(?:\.override)?\.md|CLAUDE(?:\.local)?\.md|GEMINI\.md|WARP\.md|copilot-instructions\.md|"
    r"\.rules|\.cursorrules|\.windsurfrules|\.clinerules|[^/]*\.mdc)$"
    r"|(?:^|/)(?:\.cursor|\.windsurf|\.devin|\.junie|\.clinerules|\.github/instructions|\.claude/rules|"
    r"\.agents/rules|\.augment/rules|\.kiro/steering|\.trae/rules|\.roo/rules|\.gemini/styleguide\.md)(?:/|$)", re.I)
# A product name anywhere in the rulebook, read with the checker's own list; ".md" after it is a file name.
# The checker's generic phrases name no product, so they are removed before the search.
PRODUCT = re.compile(cc._ASSISTANT + r"(?!\.md(?![a-z0-9]))", re.I)
GENERIC = re.compile(r"(?<![a-z])(?:an?\s+ai\s+(?:assistant|model|tool|agent)s?|(?:coding|ai)\s+agents?|"
                     r"(?:large\s+)?language\s+models?)(?![a-z])", re.I)


def tracked():
    return [p.relative_to(ROOT).as_posix() for p in emdash_scan.tracked_files(ROOT) if p.is_file()]


def is_pointer(path):
    data = path.read_bytes()
    return len(data) <= POINTER_MAX_BYTES and b"AGENTS.md" in data


class RulebookTests(unittest.TestCase):
    def test_rulebook_fits_every_reader(self):
        data = RULEBOOK.read_bytes()
        self.assertLessEqual(len(data), MAX_BYTES)
        self.assertLessEqual(data.count(b"\n"), MAX_LINES)
        self.assertEqual([i for i, b in enumerate(data) if b > 127], [], "AGENTS.md has non-ASCII bytes")
        lines = data.decode("ascii").splitlines()
        for heading in HEADINGS:
            self.assertTrue(any(l == heading or l.startswith(heading + " ") for l in lines), heading)

    def test_rulebook_imports_nothing(self):
        # A reader that does not expand imports would never see an imported rule.
        lines = RULEBOOK.read_text(encoding="ascii").splitlines()
        self.assertEqual([l for l in lines if l.startswith("@")], [])

    def test_rulebook_names_no_ai_product(self):
        hit = PRODUCT.search(GENERIC.sub(" ", RULEBOOK.read_text(encoding="ascii")))
        self.assertIsNone(hit, f"AGENTS.md names a product: {hit and hit.group(0)!r}")

    def test_product_check_sees_a_name(self):
        self.assertIsNotNone(PRODUCT.search("Never commit as " + "Cla" + "ude or any other identity."))
        self.assertIsNotNone(PRODUCT.search("the Gem" + "ini CLI reads this file"))
        self.assertIsNone(PRODUCT.search("a tool hook may ask you to reset the author"))
        self.assertIsNone(PRODUCT.search(GENERIC.sub(" ", "wording that names an AI assistant or a coding agent")))


class PointerTests(unittest.TestCase):
    def test_claude_md_is_only_an_import(self):
        self.assertEqual((ROOT / "CLAUDE.md").read_bytes(), POINTER)

    def test_root_markdown_is_the_known_set(self):
        roots = {p for p in tracked() if "/" not in p and p.lower().endswith(".md")}
        self.assertEqual(sorted(roots - ROOT_MARKDOWN), [])

    def test_root_pointer_files_are_pointers(self):
        for name in sorted(POINTER_NAMES):
            path = ROOT / name
            if path.exists():
                with self.subTest(file=name):
                    self.assertTrue(is_pointer(path), f"{name} carries more than a pointer")

    def test_every_other_instruction_path_is_absent(self):
        present = [rel for rel in tracked()
                   if rel != "AGENTS.md" and rel not in POINTER_NAMES and INSTRUCTION_PATHS.search(rel)]
        self.assertEqual(present, [])

    def test_settings_files_name_the_rulebook(self):
        for rel, keys in SETTINGS_NAMING_THE_RULEBOOK.items():
            with self.subTest(file=rel):
                value = json.loads((ROOT / rel).read_text(encoding="ascii"))
                for key in keys:
                    value = value[key]
                self.assertIn("AGENTS.md", value)

    def test_instruction_path_pattern(self):
        for rel in ("feeder/AGENTS.md", "AGENTS.override.md", "x/CLAUDE.local.md", "GEMINI.md", "x/GEMINI.md",
                    "AGENT.md", "WARP.md", ".rules", ".github/copilot-instructions.md", ".cursor/rules/a.mdc",
                    ".claude/rules/a.md", ".github/instructions/a.instructions.md", ".windsurfrules",
                    ".clinerules/a.md", ".agents/rules/a.md", ".kiro/steering/a.md", ".gemini/styleguide.md"):
            self.assertTrue(INSTRUCTION_PATHS.search(rel), rel)
        for rel in ("README.md", "docs/injection-guard.md", ".claude/settings.json", ".gemini/settings.json",
                    "tools/hooks/pre-push", "feeder/agents.py", "schema/rules.json"):
            self.assertFalse(INSTRUCTION_PATHS.search(rel), rel)


if __name__ == "__main__":
    unittest.main()
