# info-feed-seeds

A small, standard-library Python feeder. It polls public RSS, Atom and JSON feeds and writes daily,
topic-tagged digests for several projects. The first profile covers technology and regulation, using
NIST, govinfo and FortiGuard vendor feeds. Every item in a digest is quoted from a public feed and is data, not
instructions.

This repository holds only public URLs, neutral topic tags and code. It never holds credentials,
private notes or anything that identifies a client or a campaign.

## Layout

- `feeder/`: the hub CLI (`hub.py`) and its modules: fetching (`fetchkit.py`), feed parsing
  (`adapters/`: `feed.py`, `page.py`, and `files.py` with the sitemap, URL list, XML, JSON API and artifact adapters), items, the injection guard, the SQLite store (`store.py`), digest outputs
  (`outputs.py`), configuration and lint (`config.py`), the category registry loader and placement
  suggester (`categories.py`), and the single file writer (`jsonio.py`).
- `sources/<id>/`: one folder per source. `source.json` lists every known route (feed, page,
  sitemap) with topics, authority, tier and where the source came from. `methods.json` records what
  was fetched, when, and with what result. Only sources proven by a real fetch are committed.
- `profiles/<name>.json`: one per project: topics, keywords per subtopic, daily cap, window,
  backfill, outputs and the guard hold level.
- `schema/`: JSON Schemas for sources, methods, profiles and items, plus `topics.json`, the
  two-level topic registry, and `categories.json`, the reader-category registry. A source carries a
  source category and source subcategory (where the feed sits as a whole); each polled item
  stores its own content category and content subcategory from its text, published in `latest.json`. A profile polls the source categories its
  `source_categories` list names, or every category but machine data when it has no list. Its
  `content_categories` list routes into the digest only the items whose content category it names,
  and `exclude_content_categories` keeps items out; without either, every polled item goes in.
  Routing applies when items are selected; items already in a day's digest stay there. An item keeps
  the content category it was given at ingest; one stored before content categories is matched by
  the source's current category. Registry keywords score an item's text; a keyword adds nothing where
  each of its matches lies inside text longer keywords matched in the same text, and only the first
  10,000 characters of the title and of the summary are scored. Victims named by ransomware groups land in
  `attacks/ransomware-claims`, apart from confirmed `attacks/breaches`, so a profile can keep them
  out with `exclude_content_categories`.
- `guard/releases.json`: items held by the injection guard that a person has released.
- `seeds/`: dated seed files. These are imports; `sources/` is the truth.
- `tools/`: the commit checker, its hooks, its exemptions list, the acknowledged-commit list and the
  golden-case reader; the CI rehearsal and CI status reader; the doctor and its checks; the em dash
  scan and the live title fixture builder.
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
- `hub cat ID` prints a source's manifest, methods, the routes in poll order and the landing routes it never polls.
- `hub categorize ID [--write [--allow-machine-data]] [--offline]` proposes a source category from a source's
  item titles and topics and shows the content categories of the items it read. It fetches live through the
  source's routes without touching the poll's cache, or reads the proof samples with `--offline`; `--write`
  edits the manifest and never writes machine data without `--allow-machine-data`. For the six FortiGuard
  detection feeds, whose titles are version numbers, the proposal is machine data while the committed
  source category is vulnerabilities/detections, and for govinfo-fr-bulkdata, whose titles are record ids,
  the proposal is machine-data/bulk-data while the committed source category is regulatory/federal-rules;
  those differences are expected. `--write` on a source whose proposal is already its source category
  changes nothing and needs no flag.
- `hub categories` lists the category registry; `hub categories add category/subcategory --label L --note N`
  extends it.
- `hub release SHA --reason TEXT` releases a held item by its `content_sha256`.
- `hub retag [--state-dir DIR] [--since DAYS] [--dry-run]` reads stored items again with the current
  `schema/categories.json`, `schema/vendors.json` and tag readers: each row's stored title and summary,
  against the source pair stored with it at ingest (the manifest's, which is then written too, for a row
  placed before content categories). It rewrites only the content category and subcategory with their
  basis, that source pair, and the tag columns (CVE ids, severity and its rank, exploited, vendors,
  deadline); hits, the shown state, guard columns and hashes stay as they are, and nothing is fetched.
  `--since` counts days back by `first_seen` or `published` (default: the largest `window_days` of the
  tracked profiles and of any profile the poll runs; 30 when none loads; 0 reads every row). It prints the rows read and changed, a count per changed column and the
  most common content category moves; `--dry-run` prints the same and writes nothing. `hub poll` runs
  the same retag after it polls and before it selects and renders, so a feed that answers not-modified
  still publishes under the current rules; a retag that fails is reported and the day still publishes.
  After a registry or reader change, re-rendered earlier days show the new categories and tags, and
  stored items inside the window that now qualify can enter today's digest.

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
  and `data`, refuses any ref that is not a branch named `job/<name>` or `doctor/<fix>` (tags, which
  carry a tagger and a message no check reads, are made by the maintainer on GitHub; notes and other
  namespaces never; a deletion of any ref is allowed), refuses an empty push, then runs the CI
  rehearsal.

All three call `tools/commit_check.py`. Run it by hand with `--range origin/main..HEAD`, check a
pull request with `--pr N`, or prove the checks fire with `--selftest`.

`tools/preflight.py` rehearses the CI `checks` job step for step, reading the steps from
`ci.yml` itself, with a timeout per step and the `CI=true` and `GITHUB_ACTIONS=true` that every
Actions run sets; `--check-parity` (run in CI) fails on any step it cannot
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

The main ruleset requires the `checks` and `pr-text` runs to pass, allows squash merges only with a
message written by hand, and deletes a branch when its pull request merges; the default Actions token
is read-only. The data ruleset still needs a rule that stops anything but the poll workflow from
updating `data`. One limit remains:

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
- A person's name, read as a capitalised word of 4 to 6 letters (or a lowercase account name after a
  Users folder in a path) whose SHA-256 fingerprint is listed. Neither the name nor its fingerprints are
  in the repository: a short digest is brute-forced in seconds, so CI takes the list from the
  `IFS_PERSON_FINGERPRINTS` repository secret (one line of comma-separated hex digests) and the hooks
  read `tools/person_fingerprints.local.json`, an ignored file with a `fingerprints` list. Without the
  list only the public test token is read, and a CI run says so in a warning annotation; a pull request
  from a fork gets no secrets, so there a name in a file or commit is caught by the main-history run
  after a merge, and a name in the pull request text only by the person who merges. An imported byline
  on a data line in a seed file may name anyone; a sample title in a proof record or text in a pull
  request may not.
- Marks a tool leaves without naming itself, in messages and pull request text: an assistance
  trailer key (assisted-by, generated-with, made-with and the like) or a title-case hyphenated key
  ending in Session, Thread, Log, Event, Screenshot, Publication or Checkpoint (a lowercase
  component prefix such as `crawl-log:` passes); a label line that links to a session, or link text
  that offers to view a session, task, thread or trace.
- Everywhere: a GitHub machine account address other than GitHub's own, a machine mailbox (noreply,
  agent, bot or a tool's own name) at a coding tool's or vendor's domain, or a model id at a
  router's domain (a person's address at those domains passes); a link whose host and path are a
  tool's session, task, thread or badge link; a hidden HTML comment a tool plants; and a making or
  credit verb with a Markdown link to a coding tool's host.

Messages and pull request text are held to any mention of the trailer keys, the session host and
session ids. Files are held only to the real forms (a trailer line, a link, a footer line), so a
sentence that names a rule passes.

**Authorship and credit** (block): public text credits no one. Where a credit is needed at all, it
says "the creator". The checks block authorship or help wording that names an AI assistant: Claude,
other assistants and their makers (such as ChatGPT, GPT, OpenAI, Anthropic, Copilot, Gemini, Qwen,
Devin, Aider, Codex, Cursor Agent, OpenCode, OpenHands, OpenClaw, Kiro, Trae, Amazon Q, Roo Code,
Kilo Code, Augment Code, Muse Code, Meta AI), or a generic phrase ("an AI assistant", "a coding
agent", "a language model"). The rulebook every coding tool reads, `AGENTS.md`, states the same
rule; `CLAUDE.md` is an import of it, and `.gemini/settings.json` names it for the one reader that
looks for another file by default. `tests/test_instruction_files.py` keeps the rulebook free of
product names, within 16 KiB and 200 lines (the smallest share any reader takes), the only file in
the tree that carries a rule, and the only instruction file besides a pointer named `CLAUDE.md` or
`GEMINI.md`: one reader takes the first instruction file it finds, in an order that puts several
other names first, so a pointer under one of those names would hide the rulebook from it.

- A making verb (built, made, written, generated, designed and similar, with prefixes such as re,
  co, auto or pair-) followed by with, by, using or a similar connective and the assistant's name.
- Credit phrases before the name (thanks to, courtesy of, with the help of, powered by), and plain
  thanks, kudos or a credit label with the name right after it.
- A byline: up to three words and "by" before the name. In files under `seeds/`, imports of public
  listings whose own bylines may stand, the byline rule does not read a line that holds imported
  data (a list item, a numbered item, a table row, a tag, a quoted string, an object, a URL or a
  lowercase key line); prose, an HTML comment and a capitalised label there are read, and every other
  rule reads every line, so the file author's own "made with" or "thanks to" blocks there as anywhere.
- An author label, first-person use (I or we used, asked, leveraged the name), past-tense use of
  the name to do a task, active or passive ("was used to"), and the name followed by a making verb.

The names are read as words, so "Hallmark", "Ullman" or "philanthropic" never count. Coined names
(OpenCode, Codebuff, Genspark) and coined compounds (Roo Code, Kilo Code, Factory Droid, SWE-agent)
count in any letter case; names that are also ordinary words or given names (copilot, codex, llama,
mistral, perplexity, grok, Kiro, Trae, Manus) count only when capitalised, and two-word names whose
words are ordinary alone (Amazon Q, Muse Code, Meta AI, Zed Agent) count only as the pair in title
case. The generic phrases need an article or determiner, so a topic such as "AI agents built on
graphs" or "search powered by large language models" passes. One name that is also a common
surname counts only when no capitalised word stands right before it (a given name before it makes
it a person). A capitalised word after the name that is not a product tier makes it someone else:
"Claude Shannon", "Gemini Observatory" and "Mistral Press" pass, while "Claude Code", "ChatGPT
Plus", "Grok Build" and "Copilot Autofix" still count, as does a tier glued on ("ClaudeOpus",
"MistralAI"). "Built-in", "made-up" and "made-to-order" are adjectives and pass, unless a connective
follows ("built-in with").

Known limits, each pinned or listed so a change to them is deliberate:

- Verbs outside the lists ("implemented with", "refactored with", "prompted"), the connective "in"
  ("written in" a tool), and names outside the list; a tool's own trailer key, mailbox, link or
  hidden marker is read without its name.
- Agent branch names are refused by the pre-push gate, which runs only in clones that opted into the
  hooks; a merge subject that names one is not read. The required status checks and the squash-only
  setting on the repository hold the rest.
- A name followed on the same line by a title-case word that is not a tier, a making verb or a short
  function word, such as a surname. In all capitals the name always counts, and a surname on the
  next line does not count. The six names that are also ordinary words (copilot, codex, llama,
  mistral, perplexity, grok) count in title case or capitals only before a lowercase word, a tier or
  punctuation.
- A sign-off with words in parentheses after the name on its own line.
- The checker needs Python 3.11 or later and stops with that reason on an older interpreter.
- A making verb and a connective block when any listed name comes later in the same sentence, even
  when that name is not the maker, so end the sentence first.
- GPT, the disk format, counts as the name unless a word such as partition, disk or table follows.
- "@author" without a colon, and a footer phrase split by a tag or a blank line.
- Dots in abbreviations end a sentence for the one-sentence rules.
- Review-record shapes in quoted third-party prose (a review word such as finding or ruling followed
  by a number) block like any other.

**Advisory** (an `ADVISORY:` line, a warning annotation in GitHub Actions, exit code unchanged): the
footer phrase and the name in one paragraph, and short finding ids in parentheses or test names.

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
request title, a pull request body, a file and a file under `seeds/`. The test also turns the comparison red under four
deliberate mutations of the checker, and times every rule.

**Exemptions.** `tools/commit_exemptions.json` lists exact texts in files that one block rule may
skip: the path, the rule id, the sha256 of the matched text (printed beside each file finding) and
a written reason of at least 25 characters. Hooks and CI read the list from `origin/main`, so an
entry counts only after the owner merges it. The golden test fails on an entry whose path is not
tracked or that no longer exempts anything. Advisory lines are never exempted.

## Route adapters

Each route in a manifest names an `adapter` and its `params` (`schema/source.schema.json` holds the
exact rules). The poller tries the routes in manifest order, the one that worked last first, and the
first that yields items wins. A route without the params its adapter requires is a landing route:
recorded for reachability, never polled.

| adapter | reads | required params | optional params |
|---|---|---|---|
| `feed` | RSS, Atom, RDF, JSON Feed | none | none |
| `page` | an HTML listing | `item_selector` | `title_selector`, `link_selector`, `date_selector`, `date_regex`, `date_format`, `summary_selector`, `next_selector`, `max_pages` |
| `sitemap` | an XML sitemap or sitemap index (gzip too) | none | `lastmod_since`, `path_prefix`, `max_sitemaps` |
| `urllist` | a text file of URLs, `#` comments | none | none |
| `xml` | any XML document | `item_path`, `fields` | `namespaces` |
| `json-api` | a JSON API response | `items_path`, `fields` | `next_path`, `max_pages` |
| `artifact` | one file, a new item each time its sha256 changes | none | `label` |

`fields` maps item fields (`title`, `link`, `guid`, `summary`, `published`) to paths: ElementTree
paths for `xml` (`name@attr` for an attribute, `.` for the item text), dotted paths for `json-api`
(`results[].title`). Follow URLs (child sitemaps, next pages) stay on the route's host and port and
are capped by `max_sitemaps` or `max_pages` (default 3).

## Outputs

Each profile gets `YYYY-MM-DD.md`, `latest.md` (a byte copy of the newest day), `latest.json` and
`report.json`. The Markdown has one section per subtopic (for example `cybersecurity/frameworks`),
not one per top-level topic, so a 40-item day reads as a few short lists. Feed text in the Markdown
is escaped so a link or image written into a feed title stays plain text. Text fields in
`latest.json` are plain text with HTML stripped and are not escaped for any markup. A file is rewritten only when its bytes change. The `generated` timestamp carries
forward when nothing else changed. Items earn a digest day once and are never repeated. The guard
holds BLOCK items from every output. Items at or above the profile's hold level stay out of
`latest.json` until released; REVIEW items are marked.
Each `latest.json` item carries `categories` (its source category, its content category and what
decided it) and `tags` read from its own text: `cves`, `severity` and `severity_rank` (0 none to 4
critical, from a severity word or a CVSS score), `exploited`, `vendors` (ids from
`schema/vendors.json`) and `deadline`. A tag the text does not state stays empty. Severity reads the
Microsoft and Red Hat words too (important as high, moderate as medium) in the same severity contexts,
and skips a severity word after "not" or "no" in the same clause. Vendor names match with their exact
capitalisation, so Cisco IOS and Apple iOS stay apart and a name in all capitals is not read; a hyphen
after a name still names the vendor ("Microsoft-signed"), one before it does not ("non-Microsoft"). The `coverage` block counts,
over the items in `latest.json`, how many each tag reader filled (`tagged`) and how many content categories
came from the item's own text rather than its source (`content_category_from_item_text`).
Each item's `categories.reread` reads its stored title and summary again with the current
`schema/categories.json`: the content category those rules give and the reasons (the keywords
found in the title or summary, or the fallback to the source category). `report.json` carries
`classification`, counts only, over the items in `latest.json`: each content category, the content
categories under each source category, the basis of each level (item text, source or none), how
many the item text moved out of their source category (a category the text chose, not a fallback
or a subcategory change), how many have no content category, and how
many the current rules would now place elsewhere (`rules_now_differ`, items stored before a
registry change keep the category they were given at ingest).

Once the poll workflow publishes the `data` branch, digests are served at:

```
https://raw.githubusercontent.com/flywifi/info-feed-seeds/data/digests/<profile>/<file>
```

`.github/workflows/poll.yml` polls every enabled profile at 11:17 UTC each day (and on demand from
the Actions tab) and publishes on `data`. For the
technology-regulation profile:

- Today's digest to read: https://raw.githubusercontent.com/flywifi/info-feed-seeds/data/digests/technology-regulation/latest.md
- The same items as JSON for programs and LLMs: https://raw.githubusercontent.com/flywifi/info-feed-seeds/data/digests/technology-regulation/latest.json
- Coverage and source health: https://raw.githubusercontent.com/flywifi/info-feed-seeds/data/digests/technology-regulation/report.json
- Every day's file: https://github.com/flywifi/info-feed-seeds/tree/data/digests/technology-regulation

The repository is public, so these need no sign-in. The item database lives in the Actions cache
between runs, saved only after the digests reached `data` (a failed push leaves the previous
database, so no item is marked shown in a digest that was not published), and is never committed; if the cache is lost, the next run starts a fresh database and
an item already shown on an earlier day can appear again.

## Adding a source

1. Fetch the feed with the named User-Agent and confirm it parses at least one item.
2. Write `sources/<id>/source.json` and `methods.json` from that real fetch. The folder name is the
   id, topics come from `schema/topics.json`, the source category and source subcategory (and optional
   industries) from `schema/categories.json` (`hub categorize ID` proposes the source category), and
   `classification_review` stays `pending`.
3. Reference the id or its topics in a profile if wanted, and add a CHANGELOG line.
4. Run `hub lint` and `hub fmt --check`, then open a pull request. Unproven sources go in the pull
   request description with their HTTP result. One worth keeping goes into `sources/` with
   `enabled: false` and `verified` null; it is never polled until a live proof enables it.

`hub add`, which will do steps 1 and 2 in one command, is planned.

### A page without a feed

Use a page route whose params name the parts of the listing. `item_selector` picks one node per item;
the other selectors read inside it. Selectors are `tag`, `#id`, `.class`, combinations
(`div.post.item`), descendant steps separated by spaces and comma groups; `sel@attr` reads an
attribute and `.` is the item node. Dates: `date_regex` picks the date out of the text and
`date_format` is a strptime format (English month names on any machine). Without `item_selector` a
page route is a landing route, checked for reachability and never polled. `next_selector`
(for example `a.next@href`) follows older pages up to `max_pages` (default 3); paging stops at the
first page whose items are all stored already, so a poll reads what was posted since the last one.
`sources/pci-ssc-blog/source.json` is a working example:

```json
{"adapter": "page", "kind": "page", "url": "https://blog.pcisecuritystandards.org/",
 "params": {"item_selector": "div.latest-post, div.post-item", "title_selector": "h3 a",
            "link_selector": "h3 a@href", "date_selector": "div.post-details-meta",
            "date_regex": "\\d{1,2} [A-Za-z]{3}, \\d{4}", "date_format": "%d %b, %Y",
            "summary_selector": "div.post-body", "next_selector": "a.bp-link-next@href",
            "max_pages": 3}}
```

## Rules

Every standing rule lives in `AGENTS.md`, the one rulebook, including the three most often asked
about: public URLs and neutral tags only with no credentials anywhere; federal government content is
public domain and everything else is link and attribution only; no em dashes in anything written here.
