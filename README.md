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

## Checks before commit, push and merge

The tracked hooks are opt-in. A clone runs them only after this one-time setting, and every clone
that pushes should set it:

```
git config core.hooksPath tools/hooks
```

- `pre-commit` checks staged files and the author identity.
- `commit-msg` refuses co-author and session trailers, generated footers and dashes in the message.
- `pre-push` checks every commit and changed file being pushed, confirms the push URL is
  github.com/flywifi/info-feed-seeds, refuses pushes to `main` and `data`, and refuses an empty
  push (anything but a branch deletion).

All three call `tools/commit_check.py`. Run it by hand with `--range origin/main..HEAD`, check a
pull request with `--pr N`, or prove the checks fire with `--selftest`.

The hooks are a convenience, not a guarantee. `git commit --no-verify` and `git push --no-verify`
skip them without any message, and a clone without the setting above runs none of them.
`IFS_SKIP_HOOKS=1` also skips them, but prints a warning block. The hub never requires the hooks:
it also runs in GitHub Actions and on pull-only clones. `python3 tools/doctor.py check` notes a
clone that can push without them.

CI (`.github/workflows/ci.yml`) runs on every pull request, every push to a job branch, and every
push to `main`:

- `checks` runs the commit checks over the whole branch and the proof check on changed source
  folders using the base branch's copy of the checker, so editing `tools/commit_check.py` in a
  pull request does not change what checks that pull request. It also runs the branch's checker
  selftest, the unit tests, `hub lint`, `hub fmt --check`, the em dash scan and the doctor. It
  runs on description edits too.
- `pr-text` checks the pull request title and description, and re-runs on every edit, so removing
  a footer turns it green without a new push.
- `main-history` runs on every push to `main` and re-checks every commit since the policy boundary,
  so a merge or edit made on github.com is caught even if an earlier run never happened. There,
  GitHub's web-flow committer passes only on a commit GitHub marks verified, merges included.
  Main cannot be rewritten, so a commit that landed with a finding is listed, with a reason, in
  `tools/main_history_known.txt` once a person accepts it; it is then reported as known and only
  new findings fail. That file is reviewed like any other check.

Two limits remain:

- **CI is advisory for now.** A red run does not block a merge until the repository owner adds a
  required status checks rule for `checks` and `pr-text` to the main ruleset. The data ruleset
  also needs a rule that stops anything but the poll workflow from updating `data`.
- **A pull request can still weaken its own CI.** A `pull_request` workflow runs the workflow file
  from the pull request itself, so a pull request that edits `ci.yml` can remove the step that
  takes the checker from the base branch. Closing that gap needs the hygiene checks moved to a
  `pull_request_target` workflow that runs the base branch's checker over git data only; that is
  planned as a separate change. Until then, any change to `ci.yml`, `tools/hooks/` or
  `tools/commit_check.py` needs a person's review, and the checker prints a notice when a range
  touches them.

`tools/audit_redgreen.py` reruns every audit probe against older copies of the checker and the
current one, side by side, as the evidence for each fix.

What the checker refuses:

- **Co-author trailer:** a line that opens with the key and a colon. Prose about co-authorship
  passes.
- **Session trailer and session link:** anywhere in a message or pull request text; in files, a
  trailer line or a real link.
- **Generated footer:** a line opening with the generated-with phrase, whatever tool it names
  (CLAUDE.md read literally); a line opening with any form of the phrase that names the assistant
  within a few words; and, anywhere, the phrase followed directly by the assistant's name. Footer
  rules also run with HTML tags removed. They are only ever added to, never narrowed.

The file rule reads CLAUDE.md the way it is worded: a trailer line, a link, a footer line. CLAUDE.md
itself names these things mid-sentence and passes. Rewrapping that paragraph so a line starts with
the quoted footer phrase would make CLAUDE.md fail the check, so keep the phrase off the start of a
line.

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
