# Changelog

All notable changes to this repository are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `AGENTS.md` is the one rulebook every coding tool reads, and `CLAUDE.md` is an import of it. The
  rulebook names no AI product, imports nothing, stays under 16 KiB and 200 lines (the smallest
  share any reader takes), and no other instruction file exists in the tree: a pointer is accepted
  only as `CLAUDE.md` or `GEMINI.md` at the root, because one reader takes the first instruction
  file it finds in an order that puts several other names before the rulebook.
  `tests/test_instruction_files.py` holds that shape (a pointer is exactly the import line) and
  checks that `.gemini/settings.json`, which points its reader at the rulebook, names exactly it.
  Job branches are named `job/<name>`, and the pre-push hook refuses any branch not named
  `job/<name>` or `doctor/<fix>` and any ref that is not such a branch (tags included, since a tag
  carries a tagger and a message no check reads), so a tool's own branch, tag or attribution notes
  never reach the repository; a deletion of any ref passes.
- The checker reads marks a tool leaves without naming itself: assistance trailer keys and
  title-case hyphenated session, thread, log, event, screenshot, publication or checkpoint keys in
  messages and pull request text; session label lines there; and everywhere a GitHub machine
  account address other than GitHub's own, a machine mailbox at a coding tool's or vendor's domain,
  a tool's session, task, thread or badge link by host and path, a hidden HTML comment a tool
  plants, and a making or credit verb with a Markdown link to a coding tool's host. The authorship
  rules also read the coined names of coding agents and their makers in any letter case, several
  given-name products in title case, two-word names as the pair in title case, and more product
  tiers (Build, Web, Autofix, Spark, Composer, Fable, Mythos and others), so a name followed by one
  of them is still read as the name. The generic phrases need an article or determiner, so a topic
  such as "AI agents built on graphs" stays ordinary text.
- In files under `seeds/`, imports of public listings, the byline rule leaves lines of imported data
  alone (list items, numbered items, table rows, tags, quoted strings, objects, URLs and key lines),
  so a feed's own byline may stand there; prose is still read, and every other rule reads every
  line.
- The authorship rules also read Qwen, Kimi, Devin, Aider, Windsurf, Replit, Jules and Junie (the
  given names and the sport only when capitalised, and never with a capitalised surname after them),
  and the phrases "coding agent", "AI agent" and "language model".
- Authorship and credit wording that names an AI assistant (Claude, other assistants or their
  makers) is blocked in commit messages, pull request text and files, and AGENTS.md states the same
  rule: public text credits no one, and says "the creator" where a credit is needed. Names are read
  as words; product names that are also ordinary words count only when capitalised; a capitalised
  non-tier word after the name (a person's surname, an observatory) makes it another name; the
  generated verb, plain thanks or credit, and passive use ("was used to") are read too, as are
  product spellings with inner capitals, a tier glued onto a name, versions such as R1 or o3,
  all-capital and title-case text, and a sign-off with the name on its own line. Every authorship
  rule stays under 0.1 s on 100,000 characters.
- The rehearsal and the checker's selftest drop the variables that point git at one repository
  (GIT_DIR and its relatives, which a hook in a linked worktree receives), so scratch repositories
  never write into the repository being checked.
- The footer line and the footer phrase allow up to 40 separators, so a padded footer that renders
  as the stock line is refused; the vertical en dash is a dash; forbidden and data paths match in
  any letter case.
- `VERSION` (0.1.0), `feeder/__init__.py` exposing `__version__`, `.gitattributes` (LF everywhere) and
  the ignore list for `.cache/`, `local/`, `logs/`, `.venv-crawl/`, `*.local.json` and `.secrets.local.json`.
- JSON Schemas for sources, methods, profiles and items under `schema/`, the two-level topic registry
  `schema/topics.json`, and a standard-library mini-validator (`feeder/schema_validate.py`).
- Classification defaults as code (`feeder/classify.py`): government, standards body and court,
  vendor self-published, and the default row; every result is written with `classification_review: pending`.
- `feeder/fetchkit.py`: paced, cached, robots-aware fetching with the named User-Agent, per-host
  intervals, Retry-After with decay, a circuit breaker that counts network errors, streaming body caps
  with a `truncated` flag, a redirect handler that records every hop and refuses https to http, and
  atomically written state.
- `feeder/jsonio.py`: the single key-ordered UTF-8 LF JSON writer with atomic replace.
- `feeder/items.py`: raw item fields, `content_sha256`, display sanitising, link re-serialisation,
  `link_key` and keyword matching with topic tags.
- `feeder/guard.py`: the offline injection pattern tier, scoring the higher of the unescaped text and
  a normalised, zero-width-stripped, confusables-folded copy; `docs/injection-guard.md` records where it
  departs from the Creator OS engine document.
- `feeder/adapters/feed.py`: RSS 2.0, RSS 1.0 (RDF), Atom and JSON Feed parsing with podcast, media
  and YouTube namespaces; documents containing `<!ENTITY` are refused.
- Offline unittest suite under `tests/` with fixtures, a repository em dash scan (`tools/emdash_scan.py`)
  and a 30-day fixture of real titles from the NIST and govinfo seed feeds (`tools/build_title_fixture.py`).
- `feeder/hub.py` with `poll`, `lint`, `fmt`, `cat` and `release`; poll exits 0 on success, 2 on a
  configuration error and 3 when every fetch failed, prints ASCII only and runs as `py -3 -I feeder\hub.py`.
- `feeder/store.py`: SQLite state (`items`, `hits`, `health`) at `.cache/feeder.db`; each day's digest is
  re-rendered from it.
- `feeder/outputs.py`: `YYYY-MM-DD.md`, `latest.md`, `latest.json` and `report.json` per profile, written
  only when bytes differ, with the `generated` timestamp carried forward, and guard handling by band.
- `feeder/config.py`: source, profile and release loading, profile matching and the `hub lint` checks.
- `feeder/jsonio.py`: text and write-if-changed helpers so every output goes through the one writer.
- Nine proven sources under `sources/` (four NIST feeds, five govinfo feeds), each with a live
  `methods.json` record.
- `profiles/technology-regulation.json` and an empty `guard/releases.json`.
- Profile schema fields `window_days`, `backfill_days`, `outputs`, `guard.hold_at` and
  `subtopic_keywords` (include and exclude per subtopic).
- `tools/commit_check.py`: commit hygiene for staged files, commit messages, pushes, commit ranges,
  changed source folders and pull request text, with a selftest. Two kinds of result: block, for
  fixed shapes (co-author and session trailer lines, trailer keys that name the assistant, session
  links and ids, footer lines, the footer phrase and the assistant's name in one sentence, em and
  en dashes, review-record tokens in public text, identity, forbidden paths, CR line endings and
  non-ASCII Python, and authorship or credit wording that names an AI assistant), and advisory, for
  the footer phrase and the name in one paragraph and short finding ids (printed as `ADVISORY:`
  lines and GitHub Actions warnings, never failing a run). Text is folded before matching
  (entities, every round of percent-encoding, terminal control sequences, compatibility forms,
  format characters, Unicode hyphens and lookalike letters) and every reading is checked. Files are
  held to the real forms: a trailer line, a link, a footer line. An empty range fails closed. Every
  author and committer must be flywifi; GitHub's web-flow committer is accepted only on commits
  already on main, and in `main-history` only on commits GitHub marks verified.
- Every check rule states what it deliberately does not read in a `Limit:` text, and
  `tests/test_commit_check_golden.py` pins a case for each limit with the golden cases in
  `tests/fixtures/golden_cases.jsonl`: planted texts, negative controls and decided wording cases,
  each with its verdict as a commit message, pull request title, pull request body and file. The
  test also times every rule on 100,000-character lines and turns red under four deliberate
  mutations of the checker.
- `tools/commit_exemptions.json`: exact matched texts in files that one block rule may skip, each
  with a written reason of at least 25 characters, read from `origin/main`; a stale entry fails the
  golden test.
- `tools/main_history_known.txt`: commits on main acknowledged after they landed; an entry counts
  only if the commit was on main before the change that added it.
- Tracked hooks in `tools/hooks/` (pre-commit, commit-msg, pre-push), opt-in with
  `git config core.hooksPath tools/hooks`, with a loud `IFS_SKIP_HOOKS=1` bypass. The push URL must
  be exactly github.com/flywifi/info-feed-seeds.
- `.github/workflows/ci.yml`: `checks` and `pr-text` on every pull request event and job-branch
  push, using the base branch's copy of the checker; `main-history` on every push to main. `checks`
  prints every golden case a change adds (`tools/golden_added.py`) and runs the golden tests on
  Python 3.12 and 3.13. Advisory until the main ruleset requires the `checks` and `pr-text` status
  checks.
- `tools/preflight.py`: a local rehearsal of the CI `checks` job read from `ci.yml`, with a timeout
  per step, the `CI` and `GITHUB_ACTIONS` values Actions sets, a fast mode, a parity check run in CI,
  and DID NOT RUN instead of a pass on a dirty tree, a missing shell or a timeout. The pre-push hook
  runs it after the commit checks, and on Windows it finds Git Bash from inside the hook.
- The pre-push hook checks every commit on the branch since `origin/main`, the range CI checks.
- `tools/ci_status.py`: the CI result for one commit through the REST API, with a result, no result
  yet and an error kept apart.
- `tools/doctor.py check` with a required lint check and an advisory hooks check; exit 4 for a
  command that is not implemented yet.
- `.claude/settings.json` hides the commit and pull request attribution lines and the session link.

### Changed

- `tools/build_title_fixture.py` keeps the feed's byline in the item schema example.
- README rewritten for the repository as it now is.
- `methods.json` attempts may carry a `sample` item (title, link, published) so the repository alone
  shows what a proof fetch parsed; every source re-proven live on 2026-10-21 with a sample.
- `latest.json` items no longer carry the raw feed `title`; `title_display` stays, and `_comment`
  says consumers must escape text fields before rendering them.

### Fixed

- Digest Markdown escapes the Markdown metacharacters in feed text, so a feed title written as a
  link or image renders as plain text instead of a live link or a remote image load.
- A link with userinfo (`https://host@other.example/`) is dropped from digests because it reads
  as the wrong host.
- Authors in `latest.json` are sanitised with e-mail addresses redacted.
- `hub poll` counts a 200 response that is not a feed as a failed fetch, so a bot wall served as
  HTML everywhere exits 3 instead of 0.
- `hub release` checks the item in the database and refuses unknown, CLEAN and BLOCK items.
- README restores the rule that nothing here identifies a client or a campaign.
- Test files use `\u` escapes; a hygiene test keeps every Python file ASCII.
- Digest Markdown also stops bare URLs and `www.` text from autolinking and stops a leading `1.`,
  `1)`, `+` or `-` from opening a nested list (checked against cmark-gfm).
