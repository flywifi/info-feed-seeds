"""One rulebook for every coding tool. AGENTS.md at the root carries every standing rule, within the
size and line count the tools that read it accept, imports nothing and names no AI product, so a new
product or model never needs an edit to it. CLAUDE.md is exactly an import of it. Every other
instruction file a tool might read, at any depth, is absent or a short pointer to AGENTS.md, and the
root holds no Markdown besides the four files named here."""
import re
import sys
import unittest

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import commit_check as cc  # noqa: E402
import emdash_scan  # noqa: E402

RULEBOOK = ROOT / "AGENTS.md"
POINTER = b"@AGENTS.md\n"
MAX_BYTES = 24 * 1024  # readers concatenate rulebooks root-down inside a 32 KiB budget
MAX_LINES = 200  # readers warn on an instruction file over 200 lines
POINTER_MAX_BYTES = 400
ROOT_MARKDOWN = {"README.md", "CHANGELOG.md", "AGENTS.md", "CLAUDE.md"}
HEADINGS = ("## How these rules are read", "## Repo lock", "## Tool defaults to switch off", "## Branches",
            "## Content rules", "## Code rules", "## Reporting")
# Files and folders coding tools read instructions from, at any depth.
INSTRUCTION_PATHS = re.compile(
    r"(?:^|/)(?:AGENTS(?:\.override)?\.md|CLAUDE(?:\.local)?\.md|GEMINI\.md|copilot-instructions\.md|"
    r"\.cursorrules|\.windsurfrules|\.clinerules|[^/]*\.mdc)$"
    r"|(?:^|/)(?:\.cursor|\.windsurf|\.devin|\.junie|\.github/instructions|\.claude/rules)/", re.I)
# A product name anywhere in the rulebook, read with the checker's own list; ".md" after it is a file name.
# The checker's generic phrases name no product, so they are removed before the search.
PRODUCT = re.compile(cc._ASSISTANT + r"(?!\.md(?![a-z0-9]))", re.I)
GENERIC = re.compile(r"(?<![a-z])(?:an?\s+ai\s+(?:assistant|model|tool|agent)s?|(?:coding|ai)\s+agents?|"
                     r"(?:large\s+)?language\s+models?)(?![a-z])", re.I)


def tracked():
    return [p.relative_to(ROOT).as_posix() for p in emdash_scan.tracked_files(ROOT) if p.is_file()]


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
        self.assertEqual(roots - ROOT_MARKDOWN, set())

    def test_every_other_instruction_file_is_a_pointer(self):
        offenders = []
        for rel in tracked():
            if rel in ("AGENTS.md", "CLAUDE.md") or not INSTRUCTION_PATHS.search(rel):
                continue
            data = (ROOT / rel).read_bytes()
            if len(data) > POINTER_MAX_BYTES or b"AGENTS.md" not in data:
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_instruction_path_pattern(self):
        for rel in ("feeder/AGENTS.md", "AGENTS.override.md", "x/CLAUDE.local.md", "GEMINI.md",
                    ".github/copilot-instructions.md", ".cursor/rules/a.mdc", ".claude/rules/a.md",
                    ".github/instructions/a.instructions.md", ".windsurfrules"):
            self.assertTrue(INSTRUCTION_PATHS.search(rel), rel)
        for rel in ("README.md", "docs/injection-guard.md", ".claude/settings.json", "tools/hooks/pre-push"):
            self.assertFalse(INSTRUCTION_PATHS.search(rel), rel)


if __name__ == "__main__":
    unittest.main()
