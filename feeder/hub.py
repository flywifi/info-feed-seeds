#!/usr/bin/env python3
"""info-feed-seeds hub: poll sources, render digests, and keep the repository's config honest.

Usage (Windows: `py -3 -I feeder\\hub.py ...`; elsewhere: `python3 feeder/hub.py ...`):

  poll (--profile P | --all) [--date D] [--only ID] [--surface actions|local] [--state-dir DIR]
       [--out-dir DIR] [--offline] [--dry-run] [--summary FILE] [--commit-msg FILE]
  lint
  fmt [--check]
  cat ID
  release SHA --reason TEXT
  categorize ID [--write [--allow-machine-data]] [--offline]
  categories [add CATEGORY/SUBCATEGORY --label L --note N [--keywords a,b]
             [--category-label L --purpose P --reason R [--machine-data]]]

poll exit codes: 0 success, even when some sources failed; 2 configuration or usage error; 3 every
fetch that was attempted failed. lint and fmt --check exit 1 when they find a defect.

Console output is ASCII only. Every file is written through feeder/jsonio.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urldefrag, urlsplit, urlunsplit

if __package__ in (None, ""):
    # Run as a script (`py -3 -I feeder\hub.py`): isolated mode puts neither this folder nor the
    # repository on sys.path, so add the repository root for the `feeder` package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feeder import ROOT, adapters, categories, extract, guard, items, jsonio, outputs  # noqa: E402
from feeder.config import (ConfigError, Repo, RELEASES_COMMENT, SHA_RE, accepts, content_routed,  # noqa: E402
                           industry_routed, item_tags, json_files, lint, selected_sources)
from feeder.schema_validate import validate  # noqa: E402
from feeder.store import DB_NAME, RETAG_COLUMNS, Store, StoreError  # noqa: E402

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

# Adapters whose follow URLs are older pages, newest first: following stops at a page with nothing new.
NEWEST_FIRST = ("page", "json-api")


def _nothing_new(store: Store, source_id: str, route: dict, raws: list) -> bool:
    """True when this route pages newest first and every item on the page is already stored, so the
    pages after it hold only items an earlier poll has."""
    if route["adapter"] not in NEWEST_FIRST or not raws:
        return False
    return all(store.item(items.content_sha256(source_id, r.guid, r.link, r.title)) for r in raws)


def _same_site(route_url: str, final_url: str | None) -> bool:
    """True when the URL that answered is the route's own site: the same host with or without a leading
    www., the same port, and the same scheme or http moved up to https. An answering URL that carries
    a user name or password is never the same site."""
    parts = urlsplit(final_url or "")
    if parts.username is not None or parts.password is not None:
        return False
    a, b = adapters._origin(route_url), adapters._origin(final_url or "")
    if a is None or b is None or not a[1]:
        return False
    same_scheme = a[0] == b[0] or (a[0], b[0]) == ("http", "https")
    return same_scheme and a[1].removeprefix("www.") == b[1].removeprefix("www.") and a[2] == b[2]


def _without_userinfo(address: str) -> str:
    """The address with any user name or password removed, so links resolved against it carry none."""
    parts = urlsplit(address)
    host = parts.netloc.rpartition("@")[2]
    if (parts.username is None and parts.password is None) or not host:
        return address   # nothing to drop, or no host would be left (its links then stay unpublishable)
    return urlunsplit(parts._replace(netloc=host))


def poll_source(fetcher, source, store: Store, day: str, now: str, reg: dict | None = None,
                vendor_names: dict | None = None, standard_names: dict | None = None) -> dict:
    """Fetch the source's configured routes in order until one answers, dispatching each body to
    its adapter and following the adapter's `follow` URLs up to the route's bound. A 304 ends the
    source's poll as not-modified: its items were stored at the earlier fetch, so later routes and
    follow URLs are not fetched. Returns a health record plus `attempted`, `failed` and `new` counts."""
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
            # Relative links resolve against the address that answered, so items cite the right host. A user
            # name or password in that address is dropped first, so the links stay publishable.
            answered = urldefrag(result.final_url or url)[0]
            base = _without_userinfo(answered)
            parsed = adapters.check_result(parse(result.body, url=base, params=params))
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
        raws = [(raw, base, result.retrieved_at) for raw in parsed["items"]]
        truncated = result.truncated
        queue = [] if _nothing_new(store, source.id, route, parsed["items"]) else list(parsed.get("follow") or [])
        budget = adapters.follow_limit(route)
        seen = {url, base}
        follow_failed = follow_refused = 0
        site = (answered == url and _without_userinfo(url) == url) or _same_site(url, answered)
        if not site:
            # The route redirected to another site: none of that page's links are followed (and none are
            # counted when following is off for the route).
            follow_refused, queue = (len(queue) if budget > 0 else 0), []
        while queue and budget > 0:
            candidate = queue.pop(0)
            # The route's own address or, after a redirect within its site (www. or not, same port), the
            # address that answered; any other host is refused and counted.
            next_url = adapters.follow_url(url, candidate) or (adapters.follow_url(base, candidate) if site else None)
            if next_url is None:
                follow_refused += 1   # another host, port or scheme, or a user name or password: never fetched, but counted
                continue
            if next_url in seen:
                continue
            seen.add(next_url)
            budget -= 1
            record["attempted"] += 1
            more = fetcher.get(next_url, same_origin=True)
            if more.error is not None or more.body is None:
                follow_failed += 1
                continue
            if adapters.follow_url(url, more.final_url) is None and (not site or adapters.follow_url(base, more.final_url) is None):
                follow_failed += 1
                continue
            try:
                extra = adapters.check_result(parse(more.body, url=next_url, params=params))
            except Exception:  # noqa: BLE001 - a bad page in the chain is a failed follow, not a crash
                follow_failed += 1
                continue
            raws += [(raw, next_url, more.retrieved_at) for raw in extra["items"]]
            truncated = truncated or more.truncated
            if not _nothing_new(store, source.id, route, extra["items"]):
                queue += list(extra.get("follow") or [])
        record["failed"] += follow_failed
        new = 0
        for raw, page_url, retrieved_at in raws:
            rec = items.build_item(source.id, page_url, raw, retrieved_at=retrieved_at)
            scan = guard.scan_item_fields(raw.title, raw.summary, *raw.authors)
            rec["guard_level"], rec["guard_score"] = scan["risk_level"], scan["total_score"]
            if reg is not None:
                # The source category places the feed; the content category places this item by its own text.
                placed = categories.content_category(reg, raw.title, raw.summary, source.source_category,
                                                     source.source_subcategory)
                rec.update(source_category=source.source_category, source_subcategory=source.source_subcategory,
                           content_category=placed["category"], content_subcategory=placed["subcategory"],
                           content_category_basis=placed["category_basis"],
                           content_subcategory_basis=placed["subcategory_basis"])
            # The source's industries, from its manifest, so an item can be routed and counted by industry.
            rec["industries"] = list(source.manifest.get("industries") or [])
            # Tags the item's own text states: CVE ids, severity, exploitation, vendors and a deadline.
            rec.update(extract.tags(items.sanitize(raw.title), items.sanitize(raw.summary), vendor_names or {},
                                    standard_names or {}))
            if store.upsert_item(rec, first_seen=now):
                new += 1
        flags = (("truncated", truncated), ("follow-partial", follow_failed), ("follow-refused", follow_refused))
        error = ",".join(flag for flag, on in flags if on) or None
        record.update(status="ok" if raws else "empty", items=len(raws), error=error, new=new)
        return record
    return record


# --------------------------------------------------------------------------- selection

def _guid_key(row: dict) -> str:
    return f"guid:{row['feed_id']}:{row['guid']}"


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

    rows = store.items_for_feeds(sorted(chosen))
    # A link one source gives to several distinct items (a shared listing page) does not identify an
    # item. It counts as shared when two or more distinct guids carry it: each post on it is then keyed
    # by its guid (or, without one, by its own hash), so a retitled post keeps its guid and stays one story.
    per_source: dict[tuple[str, str], set] = {}
    for row in rows:
        if row["link_key"] and row["guid"]:
            per_source.setdefault((row["feed_id"], row["link_key"]), set()).add(row["guid"])
    # A guid already shown stays shown, so a retitled post on a shared link is not published again.
    taken_keys |= {_guid_key(row) for row in rows if row["content_sha256"] in taken and row["guid"]}
    groups: dict[str, list[tuple[dict, list[str]]]] = {}
    for row in rows:
        if row["content_sha256"] in taken:
            continue
        ref = (row["published"] or row["first_seen"])[:10]
        if not earliest <= ref <= day:
            continue
        if (not accepts(profile, row) or not content_routed(profile, chosen[row["feed_id"]], row)
                or not industry_routed(profile, chosen[row["feed_id"]], row)):
            continue
        tags, _ = item_tags(profile, chosen[row["feed_id"]], row)
        shared = len(per_source.get((row["feed_id"], row["link_key"]), ())) > 1
        if shared:
            key = _guid_key(row) if row["guid"] else row["content_sha256"]
        else:
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


# --------------------------------------------------------------------------- retag

def _pair_text(category, subcategory) -> str:
    return (f"{category}/{subcategory}" if subcategory else category) if category else "none"


def retag_window(repo: Repo) -> int:
    """The default retag window in days: the largest window_days of the tracked profiles (30 when none
    loads); the poll also counts every profile it runs, local ones included."""
    days = []
    for name in repo.profile_names():
        try:
            days.append(int(repo.load_profile(name).get("window_days", 0)))
        except (ConfigError, TypeError, ValueError):
            continue
    return max(days) if days else 30


def retag_rows(store: Store, repo: Repo, sources: dict, today: date, since_days: int, *, write: bool = True) -> dict:
    """Read stored rows again with the current category registry and tag readers, from the same inputs
    the renderer's reread uses: the stored title and summary, against the source pair stored at ingest
    (the manifest's pair, which is then written too, for a row placed before content categories). Only
    the category, basis and tag columns change; nothing is fetched. `since_days` 0 reads every row.
    Returns {"read", "changed", "columns": {name: count}, "moves": {(old, new): count}}."""
    reg = repo.categories()
    vendor_names = extract.load_vendors(repo.schema_dir / "vendors.json") or {}
    standard_names = extract.load_standards(repo.schema_dir / "standards.json") or {}
    every = since_days == 0 or since_days >= (today - date.min).days
    cutoff = None if every else (today - timedelta(days=since_days)).isoformat()
    columns: dict[str, int] = {}
    moves: dict[tuple[str, str], int] = {}
    read = changed = 0
    for row in store.rows_since(cutoff, today.isoformat()):
        read += 1
        new: dict = {}
        if reg is not None:
            if row.get("content_category_basis") is not None:
                src_cat, src_sub = row.get("source_category"), row.get("source_subcategory")
            else:
                src = sources.get(row["feed_id"])
                manifest = src.manifest if src is not None else {}
                src_cat, src_sub = manifest.get("source_category"), manifest.get("source_subcategory")
                new.update(source_category=src_cat, source_subcategory=src_sub)
            placed = categories.content_category(reg, row.get("title"), row.get("summary"), src_cat, src_sub)
            new.update(content_category=placed["category"], content_subcategory=placed["subcategory"],
                       content_category_basis=placed["category_basis"],
                       content_subcategory_basis=placed["subcategory_basis"])
        src = sources.get(row["feed_id"])
        if src is not None:   # the manifest is the source of truth; a row whose feed is gone keeps its own
            new["industries"] = list(src.manifest.get("industries") or [])
        new.update(extract.tags(items.sanitize(row.get("title")), items.sanitize(row.get("summary")), vendor_names,
                                standard_names))
        diff = {name: value for name, value in new.items() if Store.stored_form(name, value) != row.get(name)}
        if not diff:
            continue
        changed += 1
        for name in diff:
            columns[name] = columns.get(name, 0) + 1
        old = _pair_text(row.get("content_category"), row.get("content_subcategory"))
        now = _pair_text(new.get("content_category", row.get("content_category")),
                         new.get("content_subcategory", row.get("content_subcategory")))
        if old != now:
            moves[(old, now)] = moves.get((old, now), 0) + 1
        if write:
            store.update_columns(row["content_sha256"], diff)
    return {"read": read, "changed": changed, "columns": columns, "moves": moves}


def report_retag(result: dict) -> None:
    say(f"retag: {result['read']} row(s) read, {result['changed']} changed")
    for name in RETAG_COLUMNS:
        if result["columns"].get(name):
            say(f"  {name}: {result['columns'][name]}")
    for (old, now), count in sorted(result["moves"].items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        say(f"  content: {old} -> {now}: {count}")


def cmd_retag(args, repo: Repo, now_fn) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else DEFAULT_STATE_DIR
    if args.since is not None and args.since < 0:
        warn("retag: --since takes 0 (every row) or a number of days")
        return EXIT_CONFIG
    if not (state_dir / DB_NAME).exists():
        warn(f"retag: {state_dir.as_posix()}/{DB_NAME} does not exist")
        return EXIT_CONFIG
    try:
        sources = repo.load_sources()
        store = Store.open(state_dir, in_memory_copy=args.dry_run)
    except (ConfigError, StoreError) as exc:
        warn(f"retag: {exc}")
        return EXIT_CONFIG
    since = retag_window(repo) if args.since is None else args.since
    result = retag_rows(store, repo, sources, now_fn().date(), since, write=not args.dry_run)
    report_retag(result)
    if args.dry_run:
        say("retag: dry run, nothing written")
        store.conn.close()
    else:
        store.close()
    return EXIT_OK


# --------------------------------------------------------------------------- rendering

def render_day(store: Store, repo: Repo, profile: dict, sources: dict, day: str, released: set[str],
               generated: str, out_dir: Path) -> dict:
    chosen = selected_sources(profile, sources)
    by_id = {s.id: s for s in chosen}
    views = []
    reg = repo.categories()
    for row in store.hits_for_day(profile["name"], day):
        src = by_id.get(row["feed_id"]) or sources.get(row["feed_id"])
        if src is not None:
            row["primary"] = item_tags(profile, src, row)[1]
        views.append(outputs.item_view(row, src, profile, released, reg))
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
    try:
        store = Store.open(state_dir, in_memory_copy=args.dry_run)
    except StoreError as exc:
        warn(f"poll: {state_dir.as_posix()}/{DB_NAME}: {exc}")
        return EXIT_CONFIG
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
        vendor_names = extract.load_vendors(repo.schema_dir / "vendors.json")
        standard_names = extract.load_standards(repo.schema_dir / "standards.json")
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
            rec = poll_source(fetcher, src, store, day, now_z, reg=repo.categories(), vendor_names=vendor_names,
                              standard_names=standard_names)
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

    # Stored rows are read again with the current registry and readers before any day is selected or
    # rendered, so a feed that answered not-modified still publishes under today's rules. The window
    # covers every profile this poll runs, local ones included. A retag that fails leaves the rows with
    # their ingest categories and tags; the day still publishes.
    window = max([retag_window(repo)] + [int(p.get("window_days", 0)) for p in profiles])
    try:
        report_retag(retag_rows(store, repo, sources, date.fromisoformat(day), window))
    except Exception as exc:  # noqa: BLE001 - the publish never depends on a reread
        warn(f"retag: skipped, {type(exc).__name__}: {exc}")

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
    try:
        store = Store.open(state_dir, in_memory_copy=True)
    except StoreError as exc:
        warn(f"release: {state_dir.as_posix()}/{DB_NAME}: {exc}")
        return EXIT_CONFIG
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


def cmd_categorize(args, repo: Repo, fetcher_factory) -> int:
    """Propose a source category for a source from its item titles and topics, and show the content
    category of each item read; --write puts the best source category in the manifest, never machine
    data unless --allow-machine-data is given. Live by default: each configured route is fetched once,
    unconditionally, by a fetcher that keeps no state, so the poll's cache and validators are never
    touched. --offline reads the proof samples instead."""
    folder = repo.sources_dir / args.id
    if not folder.is_dir():
        warn(f"categorize: no source folder sources/{args.id}")
        return EXIT_CONFIG
    reg = repo.categories()
    if reg is None:
        warn("categorize: schema/categories.json is missing or not valid JSON")
        return EXIT_CONFIG
    try:
        src = repo.load_source(folder)
    except ConfigError as exc:
        warn(f"categorize: {exc}")
        return EXIT_CONFIG
    titles: list[str] = []
    summaries: list[str] = []
    origin = "nothing (every route failed)"
    if args.offline:
        titles = [a["sample"]["title"] for rec in (src.methods or {}).get("routes", [])
                  for a in rec.get("attempts", []) if a.get("sample")]
        summaries = [""] * len(titles)
        origin = "proof samples"
    else:
        fetcher = fetcher_factory(None)   # no state directory: nothing read or saved
        for route in src.poll_routes():
            result = fetcher.get(route["url"], conditional=False)
            if result.error is not None or result.body is None:
                say(f"categorize: {route['url']}: {result.error or 'no body'}")
                continue
            try:
                parsed = adapters.ADAPTERS[route["adapter"]](result.body, url=route["url"], params=route.get("params") or {})
            except adapters.AdapterError as exc:
                say(f"categorize: {route['url']}: {type(exc).__name__}")
                continue
            titles, origin = [raw.title for raw in parsed["items"]], route["url"]
            summaries = [items.sanitize(raw.summary) for raw in parsed["items"]]
            break
    current = f"{src.source_category}/{src.source_subcategory}" if src.source_category else "none"
    say(f"categorize {src.id}: source category {current}; read {len(titles)} title(s) from {origin}")
    found = categories.suggest(reg, titles, src.topics)
    for s in found[:3]:
        say(f"  suggest source category {s['ref']} (score {s['score']}): {'; '.join(s['reasons'])}")
    if not found:
        say("  no suggestion; pick a source category from `hub categories` and set it by hand")
    spread: dict[tuple[str, str, str, str], int] = {}
    for title, summary in zip(titles, summaries):
        placed = categories.content_category(reg, title, summary, src.source_category, src.source_subcategory)
        key = (placed["category"] or "none", placed["subcategory"] or "none", placed["category_basis"], placed["subcategory_basis"])
        spread[key] = spread.get(key, 0) + 1
    for (cat, subcat, cat_basis, sub_basis), count in sorted(spread.items(), key=lambda kv: (-kv[1], kv[0])):
        say(f"  content {cat}/{subcat}: {count} item(s); category from the {cat_basis}, subcategory from the {sub_basis}")
    if not args.write:
        return EXIT_OK
    if found and found[0]["ref"] == current:
        say(f"categorize: source category {current} unchanged")
        return EXIT_OK   # nothing to write, so no --allow-machine-data is needed
    writable = [s for s in found if args.allow_machine_data or categories.digest_default(reg, s["ref"].split("/")[0])]
    if found and not writable:
        say(f"categorize: {found[0]['ref']} is machine data, which no profile polls by default; "
            "give --allow-machine-data to write it")
    if not writable:
        return EXIT_DEFECT
    if writable[0]["ref"] == current:
        say(f"categorize: source category {current} unchanged")
        return EXIT_OK   # the best proposal it may write is the one it has
    best_cat, best_sub = writable[0]["ref"].split("/")
    manifest = dict(src.manifest, source_category=best_cat, source_subcategory=best_sub)
    jsonio.dump_json(folder / ("source.local.json" if src.local else "source.json"), manifest)
    say(f"categorize: wrote source category {writable[0]['ref']} to sources/{src.id}; check it before committing")
    return EXIT_OK


def cmd_categories(args, repo: Repo, now_fn) -> int:
    reg = repo.categories()
    if reg is None:
        warn("categories: schema/categories.json is missing or not valid JSON")
        return EXIT_CONFIG
    if args.action == "add":
        doubled = categories.duplicate_keys(repo.categories_file)
        if doubled:
            warn(f"categories add: schema/categories.json repeats the key(s) {', '.join(sorted(set(doubled)))}; "
                 "fix the file first, a rewrite would drop one of them")
            return EXIT_CONFIG
        try:
            new = categories.add_entry(
                reg, args.ref, label=args.label, note=args.note, today=now_fn().date().isoformat(),
                keywords=(args.keywords or "").split(","), category_label=args.category_label,
                purpose=args.purpose, reason=args.reason, machine_data=args.machine_data)
        except ValueError as exc:
            warn(f"categories add: {exc}")
            return EXIT_CONFIG
        errors = validate(new, repo.schema("categories.schema.json")) + categories.registry_problems(new)
        if errors:
            warn("categories add: the result would not validate: " + "; ".join(errors))
            return EXIT_CONFIG
        jsonio.dump_json(repo.categories_file, new)
        say(f"categories: added {args.ref} to schema/categories.json; add a CHANGELOG line")
        return EXIT_OK
    for cid, entry in categories.ordered(reg):
        shown = "" if entry["digest_default"] else " (shown only when a profile names it)"
        say(f"{entry['order']}. {cid}: {entry['label']}{shown}")
        for sid, s in sorted(entry["subcategories"].items(), key=lambda kv: kv[1]["order"]):
            say(f"   {cid}/{sid}: {s['label']}, {s['note']}")
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
    rt = sub.add_parser("retag", help="read stored items again with the current categories and tag readers")
    rt.add_argument("--state-dir", help="where feeder.db lives (default .cache)")
    rt.add_argument("--since", type=int, help="days back by first_seen or published (default: the largest "
                    "window_days of any profile; 0 reads every row)")
    rt.add_argument("--dry-run", action="store_true", help="print what would change and write nothing")
    sub.add_parser("lint", help="validate sources, profiles and releases")
    fmt = sub.add_parser("fmt", help="rewrite config JSON in canonical form")
    fmt.add_argument("--check", action="store_true")
    cat = sub.add_parser("cat", help="print a source's manifest and methods")
    cat.add_argument("id")
    rel = sub.add_parser("release", help="release an item held by the guard")
    rel.add_argument("sha")
    rel.add_argument("--reason", required=True)
    rel.add_argument("--state-dir", help="where feeder.db lives (default .cache)")
    cz = sub.add_parser("categorize", help="propose a source category and show item content categories")
    cz.add_argument("id")
    cz.add_argument("--write", action="store_true", help="write the best source category into the manifest")
    cz.add_argument("--offline", action="store_true", help="read the proof samples instead of fetching")
    cz.add_argument("--allow-machine-data", action="store_true", help="let --write put the source in machine data")
    cats = sub.add_parser("categories", help="list the category registry, or add an entry to it")
    cats_sub = cats.add_subparsers(dest="action")
    add = cats_sub.add_parser("add", help="add category/subcategory (a new category needs its label, purpose and reason)")
    add.add_argument("ref", help="category/subcategory")
    add.add_argument("--label", required=True, help="the subcategory's label")
    add.add_argument("--note", required=True, help="one line on what belongs in the subcategory")
    add.add_argument("--keywords", help="comma-separated title keywords for hub categorize")
    add.add_argument("--category-label")
    add.add_argument("--purpose", help="a new category's reader purpose")
    add.add_argument("--reason", help="why a new category is needed")
    add.add_argument("--machine-data", action="store_true", help="a new category polled only by a profile that names it")
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
    if args.command == "retag":
        return cmd_retag(args, repo, now_fn)
    if args.command == "lint":
        return cmd_lint(args, repo)
    if args.command == "fmt":
        return cmd_fmt(args, repo)
    if args.command == "cat":
        return cmd_cat(args, repo)
    if args.command == "release":
        return cmd_release(args, repo, now_fn)
    if args.command == "categorize":
        return cmd_categorize(args, repo, fetcher_factory or default_fetcher_factory)
    if args.command == "categories":
        return cmd_categories(args, repo, now_fn)
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
