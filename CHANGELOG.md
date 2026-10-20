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
