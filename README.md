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
  new findings fail. An entry counts only if the commit was already on main before the change that
  added the entry, so a pull request cannot acknowledge its own bad commit. That file is reviewed
  like any other check.

Two limits remain:

- **CI is advisory for now.** A red run does not block a merge until the repository owner adds a
  required status checks rule for `checks` and `pr-text` to the main ruleset. The data ruleset
  also needs a rule that stops anything but the poll workflow from updating `data`.
- **A pull request can still weaken its own CI.** A `pull_request` workflow runs the workflow file
  from the pull request itself, so a pull request that edits `ci.yml` can remove the step that
  takes the checker from the base branch. Closing that gap needs the hygiene checks moved to a
  `pull_request_target` workflow that runs the base branch's checker over git data only; that is
  planned as a separate change. Until then, any change to `ci.yml`, `tools/hooks/`,
  `tools/commit_check.py`, `tools/main_history_known.txt`, the corpora and their lock
  (`tests/fixtures/*_corpus.jsonl`, `pending_ruling.jsonl`, `corpus.lock`), the baseline checker
  (`tests/fixtures/checker_baseline.py`), `tests/fixtures/renamed_ids.txt`, `tests/test_corpus.py`,
  `tests/test_no_narrowing.py` or `tools/corpus_added.py`
  needs a person's review, and the checker prints a notice when a range touches them.

`tools/audit_redgreen.py` reruns every audit probe against older copies of the checker and the
current one, side by side, as the evidence for each fix.

What the checker refuses:

- **Co-author trailer:** a trailer line (the key and then a colon or an equals sign, at the start
  of a line or after a bullet, a number or a short label) and the key followed by an address in
  angle brackets, everywhere; in messages and pull request text, also the key joined by hyphens
  anywhere. Prose such as "co-authored by Smith" passes.
- **Session trailer, link and id:** in messages and pull request text, any mention, the host
  written with spaces around its dot included; in files, a trailer line, a real link, or a bare
  session id ("session_" and 20 or more letters or digits, hyphens allowed between them).
- **Footer:** the phrase is "generated" followed by with, by, using, via or "w/". Refused: a line
  opening with the phrase in any case, after an optional label such as "Note:", whatever tool it
  names (finding 7, read literally and failing closed until it is ruled on); and, anywhere, the
  phrase followed by the assistant's name (the name may run into "AI" or digits) directly, within a
  few words before "code" or in parentheses, later in the same sentence within 200 characters
  (across single line breaks and dots inside words or numbers), or later in the same paragraph
  within 1,000 characters (a bound for speed only). Footer rules also run with HTML tags removed.
  There are no exceptions in the rules. One file is pinned by path and sha256 because it describes
  the rules in the words the rules refuse: the baseline checker. Any change to it makes it an
  ordinary file again. Every text that might pass although the rules refuse it waits in
  `tests/fixtures/pending_ruling.jsonl` for Matt's ruling, and is refused until then.
- **Authorship statements:** commit messages, pull request text and files say what changed, why,
  and how it works, never which tool wrote them (Matt's ruling of 2026-10-23). Refused anywhere: a
  making verb (built, made, created, written, authored, assisted and similar) followed by with, by,
  using, via, w/, through or alongside and the assistant's name later in the same sentence; "thanks
  to", "courtesy of", "with the help of", "with help from", "powered by" or "in collaboration with"
  directly before the name; "I" or "we" used, asked or let the name, or "using" the name followed by
  "I" or "we"; and the name followed by wrote, built, made, created, authored, drafted, coded,
  produced, generated or helped me or us. A feature that uses the assistant may name it in the
  present tense: "the classifier calls the API", "each item is sent to it", "it summarizes each
  item". Describe the step, not the authorship.
- **Hidden characters:** text is folded before matching: entities, percent-encoding,
  compatibility forms, format characters, combining and enclosing marks, Hangul fillers, the
  blank braille pattern, the null notehead, ideographic dots, Unicode hyphens and lookalike letters
  (Cyrillic, Greek, Armenian, Cherokee, Lisu and Latin small capitals).

**What may and may not pass is fixed by corpora, not by the rules.**
`tests/fixtures/refused_corpus.jsonl` holds every string an audit proved must be refused, and
`tests/fixtures/allowed_corpus.jsonl` holds prose and real commit messages and pull request bodies
that must pass. `tests/fixtures/pending_ruling.jsonl` holds text whose verdict waits on a ruling;
the tests print its current verdicts without asserting them. The text is base64 so no scanner sees
it. A rule may change only when both corpora still pass, and the corpora only grow:
`tests/fixtures/corpus.lock` pins every id to its kind, its exact flag, the places it is checked
in and the sha256 of its text. A test fails if a locked id is missing or any of those changed, and
once main has the lock, every row on main must appear unchanged (a pending row may change kind
when its ruling is made). New ids are appended to the lock in the same commit. Every rule also has
to finish within a second on a 100,000-character line.

**Nothing may pass that the baseline refused.** `tests/fixtures/checker_baseline.py` is a copy of
the checker at `512e818`, pinned by its sha256. `tests/test_no_narrowing.py` builds about 140,000
variants with fixed seeds in named dimensions (separator runs of 0 to 40, 41, 64, 100 and 200
characters, line-break splits, prefixes and leading spaces, underscore-joined prefixes, every
lookalike letter the baseline folds, sentence openings and suffixes, sentence gaps of 0 to 80
characters, the refused corpus, every shape after 8,000, 20,000 and 100,000 characters of filler
and at the end of a 300-line body, and 20,000 random combinations), each with a minimum count. It
fails if the baseline refuses a variant that the current checker lets through, in a message, a PR
title, a PR body or a file, and also runs a sample through `check_range` (files at `.md`,
`.jsonl`, `.py` and dotfile paths), `check_pr_event` (title and body) and `check_message_text`.
Only an exact pending text is skipped, and every pending text must be refused. Every allowed entry
must also pass the baseline. CI prints the decoded text of every allowed and pending line a change
adds, using the base branch's copy of `tools/corpus_added.py`. The baseline changes only in a
commit that changes nothing else.

Next step after this change merges: a second baseline pinned to main's checker, so the test also
fails on anything main refuses that a later change lets through.

The test that compares the lock with main's copy skips only while main exists and has no lock; a
missing `origin/main` or any other git error fails it. It starts guarding once this change merges,
and it protects main only when the main ruleset requires the `checks` status check (F1, for the
repository owner) and CI runs it against the base branch.

The file rule reads CLAUDE.md the way it is worded: a trailer line, a link, a footer line. CLAUDE.md
itself names these things mid-sentence and passes, with no pin, because of how its commit-rules
paragraph is laid out. Keep three things as they are: the quoted footer phrase never starts a line;
the co-author key comes last in that sentence, so no sentence pairs it with the assistant's name;
and a blank line separates that bullet from the next one, which names the assistant, so the two
never share a paragraph.

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
