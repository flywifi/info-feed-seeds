#!/usr/bin/env python3
"""info-feed-seeds hub: poll sources, render digests, and keep the repository's config honest.

Usage (Windows: `py -3 -I feeder\\hub.py ...`; elsewhere: `python3 feeder/hub.py ...`):

  poll (--profile P | --all) [--date D] [--only ID] [--surface actions|local] [--state-dir DIR]
       [--out-dir DIR] [--offline] [--dry-run] [--summary FILE] [--commit-msg FILE]
  lint
  fmt [--check]
  cat ID
  release SHA --reason TEXT

poll exit codes: 0 success, even when some sources failed; 2 configuration or usage error; 3 every
fetch that was attempted failed. lint and fmt --check exit 1 when they find a defect.

Console output is ASCII only. Every file is written through feeder/jsonio.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    # Run as a script (`py -3 -I feeder\hub.py`): isolated mode puts neither this folder nor the
    # repository on sys.path, so add the repository root for the `feeder` package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feeder import ROOT, adapters, guard, items, jsonio, outputs  # noqa: E402
from feeder.config import (ConfigError, Repo, RELEASES_COMMENT, SHA_RE, accepts, item_tags,  # noqa: E402
                           json_files, lint, selected_sources)
from feeder.schema_validate import validate  # noqa: E402
from feeder.store import DB_NAME, Store  # noqa: E402

EXIT_OK, EXIT_DEFECT, EXIT_CONFIG, EXIT_ALL_FAILED = 0, 1, 2, 3
DEFAULT_STATE_DIR = ROOT / ".cache"
DEFAULT_OUT_DIR = ROOT / "local" / "digests"


def say(message: str = "") -> None:
    """Print ASCII only; anything else is backslash-escaped."""
    sys.stdout.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def warn(message: str) -> None:
    sys.stderr.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_fetcher_factory(state_dir: Path):
    from feeder.fetchkit import Fetcher
    return Fetcher(state_dir=state_dir)


# --------------------------------------------------------------------------- polling one source

def poll_source(fetcher, source, store: Store, day: str, now: str) -> dict:
    """Fetch the source's configured routes in order until one answers, dispatching each body to
    its adapter and following the adapter's `follow` URLs up to the route's bound. Returns a
    health record plus `attempted`, `failed` and `new` counts."""
    record = {"status": "error", "http_status": None, "items": None, "error": "no configured route",
              "route_url": None, "source_label": None, "attempted": 0, "failed": 0, "new": 0}
    for route in source.poll_routes():
        url = route["url"]
        parse = adapters.ADAPTERS[route["adapter"]]
        params = route.get("params") or {}
        record["attempted"] += 1
        result = fetcher.get(url)
        record.update(route_url=url, http_status=result.status or None, source_label=result.source_label)
        if result.error is not None:
            record["failed"] += 1
            record.update(status=result.error, error=result.error, items=None)
            continue
        if result.status == 304 or result.body is None:
            record.update(status="not-modified", error=None, items=None)
            return record
        try:
            parsed = parse(result.body, url=url, params=params)
        except adapters.AdapterError as exc:
            # A 200 that the adapter cannot read (a bot wall, an error page) is a failure, not a response.
            record["failed"] += 1
            record.update(status="parse-error", error=type(exc).__name__, items=None)
            continue
        except Exception as exc:  # noqa: BLE001 - one broken adapter must not stop the poll
            record["failed"] += 1
            record.update(status="adapter-crash", error=type(exc).__name__, items=None)
            continue
        # Every item remembers the page that held it and that fetch's time, so a digest line cites its capture.
        raws = [(raw, url, result.retrieved_at) for raw in parsed["items"]]
        truncated = result.truncated
        queue = [u for u in (parsed.get("follow") or [])]
        budget = adapters.follow_limit(route)
        seen = {url}
        follow_failed = 0
        while queue and budget > 0:
            next_url = adapters.follow_url(url, queue.pop(0))
            if next_url is None or next_url in seen:
                continue
            seen.add(next_url)
            budget -= 1
            record["attempted"] += 1
            more = fetcher.get(next_url)
            if more.error is not None or more.body is None:
                follow_failed += 1
                continue
            if adapters.follow_url(url, more.final_url) is None:
                follow_failed += 1
                continue
            try:
                extra = parse(more.body, url=next_url, params=params)
            except Exception:  # noqa: BLE001 - a bad page in the chain is a failed follow, not a crash
                follow_failed += 1
                continue
            raws += [(raw, next_url, more.retrieved_at) for raw in extra["items"]]
            truncated = truncated or more.truncated
            queue += [u for u in (extra.get("follow") or [])]
        record["failed"] += follow_failed
        new = 0
        for raw, page_url, retrieved_at in raws:
            rec = items.build_item(source.id, page_url, raw, retrieved_at=retrieved_at)
            scan = guard.scan_item_fields(raw.title, raw.summary, *raw.authors)
            rec["guard_level"], rec["guard_score"] = scan["risk_level"], scan["total_score"]
            if store.upsert_item(rec, first_seen=now):
                new += 1
        error = "truncated" if truncated else ("follow-partial" if follow_failed else None)
        record.update(status="ok" if raws else "empty", items=len(raws), error=error, new=new)
        return record
    return record


# --------------------------------------------------------------------------- selection

def select_for_day(store: Store, profile: dict, sources: dict, day: str) -> int:
    """Give new eligible items a hit on `day`, up to the daily cap. Returns the hits added."""
    name = profile["name"]
    chosen = {s.id: s for s in selected_sources(profile, sources)}
    taken = store.hit_shas(name)
    taken_keys = store.hit_link_keys(name)
    existing = store.hits_for_day(name, day)
    visible = sum(1 for r in existing if r["guard_level"] != guard.BLOCK)
    day_dt = date.fromisoformat(day)
    earliest = (day_dt - timedelta(days=int(profile["window_days"]))).isoformat()

    groups: dict[str, list[tuple[dict, list[str]]]] = {}
    for row in store.items_for_feeds(sorted(chosen)):
        if row["content_sha256"] in taken:
            continue
        ref = (row["published"] or row["first_seen"])[:10]
        if not earliest <= ref <= day:
            continue
        if not accepts(profile, row):
            continue
        tags, _ = item_tags(profile, chosen[row["feed_id"]], row)
        key = row["link_key"] or row["content_sha256"]
        groups.setdefault(key, []).append((row, tags))

    ordered = []
    for key, members in groups.items():
        members.sort(key=lambda m: (m[0]["feed_id"], m[0]["content_sha256"]))
        lead = members[0][0]
        ordered.append(((outputs._neg(lead["published"] or lead["first_seen"]), lead["feed_id"],
                         lead["content_sha256"]), key, members))
    ordered.sort(key=lambda entry: entry[0])

    added = 0
    for _, key, members in ordered:
        if key in taken_keys:
            continue
        row = members[0][0]
        tags = sorted({t for _, ts in members for t in ts})
        if row["guard_level"] == guard.BLOCK:
            store.add_hit(name, row["content_sha256"], day, tags)
        elif visible < int(profile["daily_cap"]):
            store.add_hit(name, row["content_sha256"], day, tags)
            visible += 1
        else:
            continue
        taken_keys.add(key)
        added += 1
    return added


# --------------------------------------------------------------------------- rendering

def render_day(store: Store, repo: Repo, profile: dict, sources: dict, day: str, released: set[str],
               generated: str, out_dir: Path) -> dict:
    chosen = selected_sources(profile, sources)
    by_id = {s.id: s for s in chosen}
    views = []
    for row in store.hits_for_day(profile["name"], day):
        src = by_id.get(row["feed_id"]) or sources.get(row["feed_id"])
        if src is not None:
            row["primary"] = item_tags(profile, src, row)[1]
        views.append(outputs.item_view(row, src, profile, released))
    health = outputs.health_entries(chosen, store.health_for_day(day))
    coverage = outputs.coverage_of(views, health)
    transitions = []
    for entry in health:
        if entry["status"] == "did-not-run":
            continue
        prev = store.previous_health(entry["feed_id"], day)
        before = prev["status"] if prev else None
        if before != entry["status"]:
            transitions.append({"feed_id": entry["feed_id"], "from": before, "to": entry["status"]})
    markdown = outputs.render_markdown(profile, day, views, health)
    latest = outputs.latest_json(profile, day, views, health, coverage, generated)
    report = outputs.report_json(profile, day, views, health, coverage, transitions, generated)
    return {"files": outputs.plan_files(out_dir, profile, day, markdown, latest, report),
            "coverage": coverage}


# --------------------------------------------------------------------------- poll command

def cmd_poll(args, repo: Repo, fetcher_factory, now_fn) -> int:
    if bool(args.profile) == bool(args.all):
        warn("poll: give exactly one of --profile P or --all")
        return EXIT_CONFIG
    now = now_fn()
    try:
        day = date.fromisoformat(args.date).isoformat() if args.date else now.date().isoformat()
    except ValueError:
        warn(f"poll: --date {args.date!r} is not YYYY-MM-DD")
        return EXIT_CONFIG
    try:
        names = [args.profile] if args.profile else [
            n for n in repo.profile_names() if repo.load_profile(n).get("enabled", True)]
        profiles = [repo.load_profile(n) for n in names]
        sources = repo.load_sources()
        released = repo.released_shas()
        schema = repo.schema("source.schema.json")
        for src in sources.values():
            errors = validate(src.manifest, schema)
            if errors or src.manifest.get("id") != src.id:
                raise ConfigError(f"sources/{src.id}: " + ("; ".join(errors) or "id does not equal the folder name"))
    except ConfigError as exc:
        warn(f"poll: {exc}")
        return EXIT_CONFIG
    if args.only and args.only not in sources:
        warn(f"poll: --only {args.only!r} is not a source id")
        return EXIT_CONFIG

    state_dir = Path(args.state_dir) if args.state_dir else DEFAULT_STATE_DIR
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    store = Store.open(state_dir, in_memory_copy=args.dry_run)
    now_z = stamp(now)

    wanted: dict[str, object] = {}
    for profile in profiles:
        for src in selected_sources(profile, sources):
            wanted[src.id] = src
    if args.only:
        wanted = {k: v for k, v in wanted.items() if k == args.only}

    attempted = failed = responded = new_items = 0
    if args.offline:
        say(f"poll {day}: offline, no fetches; rendering from {state_dir.as_posix()}")
    else:
        fetcher = None
        for sid in sorted(wanted):
            src = wanted[sid]
            runs_on = src.manifest.get("runs_on") or ["actions", "local"]
            reason = None
            if not src.manifest.get("enabled", True):
                reason = "disabled"
            elif args.surface not in runs_on:
                reason = f"runs_on {','.join(runs_on)} excludes {args.surface}"
            elif not src.poll_routes():
                reason = "no configured route"
            if reason:
                store.record_health(sid, day, status="skipped", http_status=None, items=None, error=reason,
                                    route_url=None, source_label=None, checked_at=now_z)
                say(f"skip {sid}: {reason}")
                continue
            if fetcher is None:
                fetcher = fetcher_factory(state_dir)
            rec = poll_source(fetcher, src, store, day, now_z)
            attempted += rec["attempted"]
            failed += rec["failed"]
            new_items += rec["new"]
            if rec["status"] in outputs.RESPONDED:
                responded += 1
            store.record_health(sid, day, status=rec["status"], http_status=rec["http_status"],
                                items=rec["items"], error=rec["error"], route_url=rec["route_url"],
                                source_label=rec["source_label"], checked_at=now_z)
            detail = f"HTTP {rec['http_status']}" if rec["http_status"] else "no response"
            count = f", {rec['items']} items ({rec['new']} new)" if rec["items"] is not None else ""
            say(f"poll {sid}: {rec['status']}, {detail}{count}")
        if fetcher is not None and not args.dry_run:
            fetcher.save()

    written: list[Path] = []
    summary_rows = []
    for profile in profiles:
        added = select_for_day(store, profile, sources, day)
        days = [day]
        for back in range(1, int(profile.get("backfill_days", 0)) + 1):
            prior = (date.fromisoformat(day) - timedelta(days=back)).isoformat()
            if prior in store.days_with_hits(profile["name"]):
                days.append(prior)
        coverage = None
        for d in sorted(days):
            rendered = render_day(store, repo, profile, sources, d, released, now_z, out_dir)
            if d == day:
                coverage = rendered["coverage"]
            if args.dry_run:
                for path in outputs.changed_files(rendered["files"]):
                    say(f"dry-run: would write {_show(path)}")
            else:
                written += outputs.write_files(rendered["files"])
        summary_rows.append((profile["name"], added, coverage))
        say(f"digest {profile['name']} {day}: {coverage['items_in_markdown']} items, "
            f"{added} new today, held by guard {coverage['held_by_guard']}")

    if args.dry_run:
        store.conn.close()
        say("dry-run: nothing written")
    else:
        store.close()
        for path in written:
            say(f"wrote {_show(path)}")
        if not written:
            say("no output changed")
        if args.summary:
            jsonio.write_text(args.summary, _summary_text(day, args, wanted, attempted, failed, responded,
                                                          new_items, summary_rows, written))
        if args.commit_msg:
            jsonio.write_text(args.commit_msg, _commit_text(day, summary_rows, responded, len(wanted),
                                                            new_items, written, args.offline))
    if attempted and failed == attempted:
        warn(f"poll: every fetch failed ({failed} of {attempted})")
        return EXIT_ALL_FAILED
    return EXIT_OK


def _show(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _summary_text(day, args, wanted, attempted, failed, responded, new_items, rows, written) -> str:
    lines = [f"## info-feed-seeds poll {day}", "",
             f"- mode: {'offline' if args.offline else 'live'}, surface {args.surface}",
             f"- sources polled: {len(wanted)}, responded: {responded}",
             f"- fetches attempted: {attempted}, failed: {failed}",
             f"- new items stored: {new_items}", f"- files written: {len(written)}", "",
             "| profile | items | new today | held by guard |", "|---|---|---|---|"]
    for name, added, cov in rows:
        lines.append(f"| {name} | {cov['items_in_markdown']} | {added} | {cov['held_by_guard']} |")
    return "\n".join(lines) + "\n"


def _commit_text(day, rows, responded, polled, new_items, written, offline) -> str:
    names = ", ".join(name for name, _, _ in rows)
    body = [f"data: poll {day} ({names})", "",
            f"Sources responded: {responded} of {polled}{' (offline run)' if offline else ''}",
            f"New items stored: {new_items}", f"Files changed: {len(written)}"]
    for name, added, cov in rows:
        body.append(f"{name}: {cov['items_in_markdown']} items, {added} new, held by guard {cov['held_by_guard']}")
    return "\n".join(body) + "\n"


# --------------------------------------------------------------------------- other commands

def cmd_lint(args, repo: Repo) -> int:
    problems = lint(repo)
    for p in problems:
        say(f"lint: {p}")
    say(f"lint: {len(problems)} problem(s) in {len(repo.source_folders())} source(s) and "
        f"{len(repo.profile_names())} profile(s)")
    return EXIT_DEFECT if problems else EXIT_OK


def cmd_fmt(args, repo: Repo) -> int:
    bad = []
    for path in json_files(repo):
        try:
            if jsonio.is_canonical(path):
                continue
            obj = jsonio.load_json(path)
        except ValueError as exc:
            say(f"fmt: {_show(path)} is not valid JSON: {exc}")
            return EXIT_CONFIG
        bad.append(path)
        if not args.check:
            jsonio.dump_json(path, obj)
    verb = "not canonical" if args.check else "rewrote"
    for path in bad:
        say(f"fmt: {verb} {_show(path)}")
    say(f"fmt: {len(bad)} file(s) {verb}")
    return EXIT_DEFECT if (args.check and bad) else EXIT_OK


def cmd_cat(args, repo: Repo) -> int:
    folder = repo.sources_dir / args.id
    if not folder.is_dir():
        warn(f"cat: no source folder sources/{args.id}")
        return EXIT_CONFIG
    try:
        src = repo.load_source(folder)
    except ConfigError as exc:
        warn(f"cat: {exc}")
        return EXIT_CONFIG
    say(f"# sources/{args.id}/{'source.local.json' if src.local else 'source.json'}")
    say(jsonio.dumps(src.manifest).rstrip("\n"))
    say(f"# sources/{args.id}/methods.json")
    say(jsonio.dumps(src.methods).rstrip("\n") if src.methods is not None else "(missing)")
    polled = [f"{r['url']} ({r['adapter']})" for r in src.poll_routes()]
    landing = [f"{r['url']} ({r['adapter']})" for r in src.manifest.get("routes", []) if not adapters.configured(r)]
    say(f"# routes in poll order: {', '.join(polled) or 'none'}")
    say(f"# landing routes, not polled: {', '.join(landing) or 'none'}")
    return EXIT_OK


def cmd_release(args, repo: Repo, now_fn) -> int:
    sha = args.sha.strip().lower()
    if not SHA_RE.match(sha):
        warn("release: SHA must be a 64 character sha256 (an item's content_sha256)")
        return EXIT_CONFIG
    reason = (args.reason or "").strip()
    if not reason:
        warn("release: --reason must not be empty")
        return EXIT_CONFIG
    try:
        data = repo.load_releases()
    except (ConfigError, ValueError) as exc:
        warn(f"release: {exc}")
        return EXIT_CONFIG
    state_dir = Path(args.state_dir) if args.state_dir else DEFAULT_STATE_DIR
    if not (state_dir / DB_NAME).exists():
        warn(f"release: no database at {state_dir.as_posix()}/{DB_NAME}; give --state-dir")
        return EXIT_CONFIG
    store = Store.open(state_dir, in_memory_copy=True)
    item = store.item(sha)
    store.conn.close()
    if item is None:
        warn(f"release: {sha} is not an item in the database; nothing to release")
        return EXIT_CONFIG
    if item["guard_level"] == guard.BLOCK:
        warn(f"release: {sha} is BLOCK (pattern score {item['guard_score']}); BLOCK is never released")
        return EXIT_CONFIG
    if item["guard_level"] == guard.CLEAN:
        warn(f"release: {sha} is CLEAN and not held; nothing to release")
        return EXIT_CONFIG
    if any(r["sha"] == sha for r in data["releases"]):
        say(f"release: {sha} is already released")
        return EXIT_OK
    data = {"_comment": data.get("_comment") or RELEASES_COMMENT,
            "releases": data["releases"] + [{"sha": sha, "date": now_fn().date().isoformat(), "reason": reason}]}
    jsonio.dump_json(repo.releases_file, data)
    say(f"release: appended {sha} ({item['guard_level']}, pattern score {item['guard_score']}) to guard/releases.json")
    return EXIT_OK


# --------------------------------------------------------------------------- entry point

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hub", description="info-feed-seeds hub")
    sub = ap.add_subparsers(dest="command", required=True)
    poll = sub.add_parser("poll", help="poll sources and render digests")
    poll.add_argument("--profile")
    poll.add_argument("--all", action="store_true")
    poll.add_argument("--date", help="digest day, YYYY-MM-DD (default: today, UTC)")
    poll.add_argument("--only", help="poll only this source id (digests are still rendered in full)")
    poll.add_argument("--surface", choices=("actions", "local"), default="local")
    poll.add_argument("--state-dir")
    poll.add_argument("--out-dir")
    poll.add_argument("--offline", action="store_true", help="no fetches; render from SQLite only")
    poll.add_argument("--dry-run", action="store_true", help="write nothing (database, outputs or state)")
    poll.add_argument("--summary", help="write a Markdown run summary to this file")
    poll.add_argument("--commit-msg", help="write a commit message for the data branch to this file")
    sub.add_parser("lint", help="validate sources, profiles and releases")
    fmt = sub.add_parser("fmt", help="rewrite config JSON in canonical form")
    fmt.add_argument("--check", action="store_true")
    cat = sub.add_parser("cat", help="print a source's manifest and methods")
    cat.add_argument("id")
    rel = sub.add_parser("release", help="release an item held by the guard")
    rel.add_argument("sha")
    rel.add_argument("--reason", required=True)
    rel.add_argument("--state-dir", help="where feeder.db lives (default .cache)")
    return ap


def main(argv: list[str] | None = None, *, root: Path | None = None, fetcher_factory=None,
         now_fn=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_CONFIG if exc.code else EXIT_OK
    repo = Repo(root=Path(root) if root is not None else ROOT)
    now_fn = now_fn or utc_now
    if args.command == "poll":
        return cmd_poll(args, repo, fetcher_factory or default_fetcher_factory, now_fn)
    if args.command == "lint":
        return cmd_lint(args, repo)
    if args.command == "fmt":
        return cmd_fmt(args, repo)
    if args.command == "cat":
        return cmd_cat(args, repo)
    if args.command == "release":
        return cmd_release(args, repo, now_fn)
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
