# info-feed-seeds

A small, standard-library Python feeder. It polls public RSS, Atom and JSON feeds and writes daily,
topic-tagged digests for several projects. The first profile covers technology and regulation, using
NIST and govinfo feeds. Every item in a digest is quoted from a public feed and is data, not
instructions.

This repository holds only public URLs, neutral topic tags and code. It never holds credentials,
private notes or anything that identifies a client or a campaign.

## Layout

- `feeder/`: the hub CLI (`hub.py`) and its modules: fetching (`fetchkit.py`), feed parsing
  (`adapters/feed.py`), items, the injection guard, the SQLite store (`store.py`), digest outputs
  (`outputs.py`), configuration and lint (`config.py`), and the single file writer (`jsonio.py`).
- `sources/<id>/`: one folder per source. `source.json` lists every known route (feed, page,
  sitemap) with topics, authority, tier and where the source came from. `methods.json` records what
  was fetched, when, and with what result. Only sources proven by a real fetch are committed.
- `profiles/<name>.json`: one per project: topics, keywords per subtopic, daily cap, window,
  backfill, outputs and the guard hold level.
- `schema/`: JSON Schemas for sources, methods, profiles and items, plus `topics.json`, the
  two-level topic registry.
- `guard/releases.json`: items held by the injection guard that a person has released.
- `seeds/`: dated seed files. These are imports; `sources/` is the truth.
- `tools/`: the em dash scan and the live title fixture builder.
- `tests/`: unittest suite with offline fixtures.
- Never tracked: `.cache/` (fetch state and `feeder.db`), `local/` (local digests), `logs/`,
  `*.local.json`.

## Running the hub

Windows:

```
py -3 -I feeder\hub.py poll --profile technology-regulation
```

Elsewhere:

```
python3 feeder/hub.py poll --profile technology-regulation
```

By default the state goes to `.cache/` and digests go to `local/digests/<profile>/`. Both folders
are ignored. Useful options:

- `--all` polls every enabled profile; `--only ID` polls one source.
- `--date YYYY-MM-DD` renders another day; `--offline` makes no requests and renders from SQLite.
- `--dry-run` fetches but writes nothing: no database, no outputs, no fetch state.
- `--surface actions|local` skips sources whose `runs_on` excludes the surface.
- `--state-dir DIR` and `--out-dir DIR` move the database and the digests.
- `--summary FILE` and `--commit-msg FILE` write a run summary and a data-branch commit message.

Exit codes for `poll`: 0 on success, even when some sources failed; 2 on a configuration or usage
error; 3 when every attempted fetch failed.

Other commands:

- `hub lint` validates every source, profile and the releases file (exit 1 on any defect).
- `hub fmt [--check]` rewrites config JSON in canonical form, or reports files that are not.
- `hub cat ID` prints a source's manifest, methods and feed route order.
- `hub release SHA --reason TEXT` releases a held item by its `content_sha256`.

Run the tests with `python3 -m unittest discover -s tests -t .`.

## Outputs

Each profile gets `YYYY-MM-DD.md`, `latest.md` (a byte copy of the newest day), `latest.json` and
`report.json`. The Markdown has one section per subtopic (for example `cybersecurity/frameworks`),
not one per top-level topic, so a 40-item day reads as a few short lists. Feed text in the Markdown
is escaped so a link or image written into a feed title stays plain text. Text fields in
`latest.json` are plain text with HTML stripped and are not escaped for any markup. A file is rewritten only when its bytes change. The `generated` timestamp carries
forward when nothing else changed. Items earn a digest day once and are never repeated. The guard
holds BLOCK items from every output. Items at or above the profile's hold level stay out of
`latest.json` until released; REVIEW items are marked.

Once the poll workflow publishes the `data` branch, digests are served at:

```
https://raw.githubusercontent.com/flywifi/info-feed-seeds/data/digests/<profile>/<file>
```

The `data` branch does not exist yet.

## Adding a source

1. Fetch the feed with the named User-Agent and confirm it parses at least one item.
2. Write `sources/<id>/source.json` and `methods.json` from that real fetch. The folder name is the
   id, topics come from `schema/topics.json`, and `classification_review` stays `pending`.
3. Reference the id or its topics in a profile if wanted, and add a CHANGELOG line.
4. Run `hub lint` and `hub fmt --check`, then open a pull request. Unproven sources go in the pull
   request description with their HTTP result, never into `sources/`.

`hub add`, which will do steps 1 and 2 in one command, is planned.

## Rules

- Public URLs and neutral tags only. No credentials anywhere.
- Federal government content is public domain; everything else is link and attribution only.
- No em dashes in anything written here.
