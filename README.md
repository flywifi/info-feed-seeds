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

- `pre-commit` checks staged files and the author and committer identity.
- `commit-msg` checks the commit message.
- `pre-push` checks every commit and changed file on the branch since `origin/main` (the range CI
  checks), confirms the push URL is github.com/flywifi/info-feed-seeds, refuses pushes to `main`
  and `data`, refuses an empty push (anything but a branch deletion), then runs the CI rehearsal.

All three call `tools/commit_check.py`. Run it by hand with `--range origin/main..HEAD`, check a
pull request with `--pr N`, or prove the checks fire with `--selftest`.

`tools/preflight.py` rehearses the CI `checks` job step for step, reading the steps from
`ci.yml` itself, with a timeout per step; `--check-parity` (run in CI) fails on any step it cannot
rehearse. It says DID NOT RUN instead of passing when the tree has uncommitted changes, HEAD is
not the commit being pushed, bash is missing or a step times out. `--fast` skips the full
unit-test step and says the rehearsal was partial; `IFS_PREFLIGHT_FAST=1` asks the hook for it,
and `IFS_SKIP_PREFLIGHT=1` skips the rehearsal with a DID NOT RUN line. Under CI both are refused.
`python3 tools/ci_status.py SHA` reads the CI result for a commit and keeps a result, no result
yet and an error apart: exit 0 green, 1 red, 2 error, 3 no result yet.

The hooks are a convenience, not a guarantee. `git commit --no-verify` and `git push --no-verify`
skip them without any message, and a clone without the setting above runs none of them.
`IFS_SKIP_HOOKS=1` also skips them, but prints a warning block. The hub never requires the hooks:
it also runs in GitHub Actions and on pull-only clones. `python3 tools/doctor.py check` notes a
clone that can push without them.

CI (`.github/workflows/ci.yml`) runs on every pull request, every push to a job branch, and every
push to `main`:

- `checks` runs the commit checks over the whole branch and the proof check on changed source
  folders using the base branch's copy of the checker, so editing `tools/commit_check.py` in a
  pull request does not change what checks that pull request. It prints every golden case the
  change adds, runs the branch's checker selftest, the unit tests, `hub lint`, `hub fmt --check`,
  the em dash scan and the doctor, and runs the golden tests again on Python 3.13. It runs on
  description edits too.
- `pr-text` checks the pull request title and description, and re-runs on every edit.
- `main-history` runs on every push to `main` and re-checks every commit since the policy boundary,
  so a merge or edit made on github.com is caught even if an earlier run never happened. There,
  GitHub's web-flow committer passes only on a commit GitHub marks verified, merges included.
  Main cannot be rewritten, so a commit that landed with a finding is listed, with a reason, in
  `tools/main_history_known.txt` once a person accepts it; it is then reported as known and only
  new findings fail. An entry counts only if the commit was already on main before the change that
  added the entry, so a pull request cannot acknowledge its own bad commit.

Two limits remain:

- **CI is advisory for now.** A red run does not block a merge until the repository owner adds a
  required status checks rule for `checks` and `pr-text` to the main ruleset. The data ruleset
  also needs a rule that stops anything but the poll workflow from updating `data`.
- **A pull request can still weaken its own CI.** A `pull_request` workflow runs the workflow file
  from the pull request itself, so a pull request that edits `ci.yml` can remove the step that
  takes the checker from the base branch. Closing that gap needs the hygiene checks moved to a
  `pull_request_target` workflow that runs the base branch's checker over git data only; that is
  planned as a separate change. Until then, any change to `ci.yml`, `tools/hooks/`,
  `tools/commit_check.py`, `tools/commit_exemptions.json`, `tools/main_history_known.txt`,
  `tools/golden_added.py`, `tests/fixtures/golden_cases.jsonl` or
  `tests/test_commit_check_golden.py` needs a person's review, and the checker prints a notice
  when a range touches them.

### What blocks and what is advisory

The checker gives two kinds of result.

**Block** (the hook or CI step fails): fixed shapes that a tool adds or that cannot be read two
ways.

- A co-author trailer line, and in messages and pull request text the hyphenated key anywhere or
  the key followed by an address.
- A session trailer line; in messages and pull request text, any mention of the session key.
- A session link, and a bare session id ("session_" and 20 or more letters or digits).
- A footer line: the footer phrase and a tool name of up to four words (or a bracketed name, with
  an optional link and label) on a line of their own, whatever tool it names.
- The footer phrase and the assistant's name in one sentence, within 200 characters.
- A trailer key that names an authorship role (ending in by, assisted, assistant, generated,
  author, authors, authored or ai) with the assistant's name after it.
- An em dash, an en dash or another dash a renderer shows as one.
- A review-record token in public text: a review word with a round, finding or ruling number, a
  severity tally such as two counts with severity words, or a capital severity id with a number.
- An author or committer other than flywifi, a path that is never tracked, CR line endings,
  non-ASCII Python, and machine output off the `data` branch.

Messages and pull request text are held to any mention of the trailer keys, the session host and
session ids. Files are held only to the real forms (a trailer line, a link, a footer line), so a
sentence that names a rule passes.

**Advisory** (an `ADVISORY:` line, a warning annotation in GitHub Actions, exit code unchanged):
wording that may claim authorship, which a person reads and rewords.

- A making verb (built, made, written, designed and similar, with prefixes such as re, co, auto or
  pair-) followed by with, by, using or a similar connective and the assistant's name.
- Credit phrases before the name: thanks to, courtesy of, with the help of, powered by.
- A byline: up to three words and "by" before the name.
- An author label, first-person use (I or we used, asked, leveraged the name), past-tense use of
  the name to do a task, and the name followed by a making verb.
- The footer phrase and the name in one paragraph, and short finding ids in parentheses or test
  names.

A feature that uses the assistant names it in the present tense and passes: "the classifier calls
the API", "each item is sent to it", "the summarize step uses it to label items".

Every rule states what it deliberately does not read in its `Limit:` text in
`tools/commit_check.py`, and `tests/test_commit_check_golden.py` pins a case for each limit, so
widening a rule is a deliberate change with a test.

**Every reading.** Text is folded before matching: HTML entities, every round of percent-encoding
(up to eight), terminal control sequences, compatibility forms, format characters, combining
marks, blank fillers, ideographic dots, Unicode hyphens and lookalike letters. Every rule runs on
every reading, with and without HTML tags, and a finding in any one counts. Every bound is small,
and a run of word characters never shares a character with a run of separators, so each rule
finishes in well under a second on a 100,000-character line.

**Golden cases.** `tests/fixtures/golden_cases.jsonl` (base64, so no scanner reads it) holds
planted texts that must block or advise, negative controls that must pass, one case for each
`Limit:`, and decided wording cases. Each case gives the verdict as a commit message, a pull
request title, a pull request body and a file. The test also turns the comparison red under four
deliberate mutations of the checker, and times every rule.

**Exemptions.** `tools/commit_exemptions.json` lists exact texts in files that one block rule may
skip: the path, the rule id, the sha256 of the matched text (printed beside each file finding) and
a written reason of at least 25 characters. Hooks and CI read the list from `origin/main`, so an
entry counts only after the owner merges it. The golden test fails on an entry whose path is not
tracked or that no longer exempts anything. Advisory lines are never exempted.

**History boundary.** Commits at or before `HISTORY_BOUNDARY` in `tools/commit_check.py` were
written before these rules; where the repository has that commit, they get the identity check
only. History is never rewritten to satisfy a check.

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
