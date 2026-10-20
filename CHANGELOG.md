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

### Changed

- `tools/build_title_fixture.py` keeps the feed's byline in the item schema example.
- README rewritten for the repository as it now is.
