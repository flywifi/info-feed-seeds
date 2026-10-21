# Changelog

All notable changes to this repository are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  changed source folders and pull request text, with a selftest. Text is normalised before matching
  (compatibility forms, format characters, Unicode hyphens, lookalike letters, entities and
  percent-encoding). Files are held to the rule as CLAUDE.md words it: no trailer line, session
  link or generated footer line. An empty range fails closed. Every author and committer must be
  flywifi; GitHub's web-flow committer is accepted only on commits already on main.
- Tracked hooks in `tools/hooks/` (pre-commit, commit-msg, pre-push), opt-in with
  `git config core.hooksPath tools/hooks`, with a loud `IFS_SKIP_HOOKS=1` bypass. The push URL must
  be exactly github.com/flywifi/info-feed-seeds.
- `.github/workflows/ci.yml`: `checks` and `pr-text` on every pull request event and job-branch
  push, using the base branch's copy of the checker; `main-history` on every push to main. Advisory
  until the main ruleset requires the `checks` and `pr-text` status checks.
- `tools/doctor.py check` with a required lint check and an advisory hooks check; exit 4 for a
  command that is not implemented yet.
- Second audit round: footer lines with an underscore after the phrase, capital lookalike letters,
  combining marks, Hangul fillers, ideographic dots, dash entities and more dash characters are
  caught; a tool-named footer must open a line, so prose about the rule passes; pre-push fails
  with no or malformed ref lines; pre-commit checks the committer; `main-history` re-checks main
  from the boundary and trusts GitHub's committer only on first-parent merges or verified
  commits; the checker bootstrap is limited to the one base that has no checker.
- Third audit round: footer rules restored and extended (the stock footer however it is wrapped,
  labelled lines, HTML-wrapped lines); co-author trailers must have trailer shape in messages too;
  GitHub's committer on main needs GitHub's verification, merges included; acknowledged commits on
  main are listed in `tools/main_history_known.txt`; the checker bootstrap is tied to PR 7;
  `tools/audit_redgreen.py` reruns every audit probe against older checkers.
- Fourth audit round: `tests/fixtures/refused_corpus.jsonl` and `allowed_corpus.jsonl` (base64)
  fix what must be refused and what must pass, and only grow; footer and co-author rules rebuilt
  against them; every rule bounded and timed on 100,000-character lines; an acknowledgement in
  `tools/main_history_known.txt` counts only after the commit landed; the red-green harness runs
  the corpora.
- Fifth audit round: the auditor's exact probe strings join the corpora (60 refused, 9 allowed);
  `tests/fixtures/pending_ruling.jsonl` holds text whose verdict waits on finding 7, with its
  verdicts printed; `tests/fixtures/corpus.lock` pins every id to its text and kind. The literal
  footer rule is case-insensitive again; a capital "Generated" naming the assistant later on the
  line is refused; files refuse numbered, labelled, equals-sign and address-bearing co-author
  forms and bare session ids; enclosing marks and the blank braille pattern are folded away.
- Sixth audit round: `tests/fixtures/checker_baseline.py` (the checker at `512e818`) and
  `tests/test_no_narrowing.py`, which fails when a generated variant the baseline refused passes;
  separator runs are unbounded again with linear line starts; any statement that something was
  made with, by or using the assistant is refused; more lookalike letters are folded; lock rows pin
  exact and where and are compared with main's; no text may be both pending and decided.

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
