#!/usr/bin/env python3
"""Fetch the NIST and govinfo seed feeds live and write the 30-day title fixture.

Usage: python3 tools/build_title_fixture.py [--days 30] [--out tests/fixtures/live/titles.json]

The fixture records, per feed, the fetch result (status, final URL, retrieved_at, truncated) and,
per item inside the window, the raw title, link, guid and published date exactly as the feed gave
them. It then scores every title with the guard and records the band counts, so the offline test
can re-score the same titles and prove the guard still agrees with the recorded counts. Everything
in the file came from a real fetch; nothing is typed in by hand.

It also writes two schema fixtures from the first NIST fetch: a methods record and an item record,
so those examples are real captures rather than invented ones.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feeder import USER_AGENT, guard, items, jsonio  # noqa: E402
from feeder.adapters import feed as feed_adapter  # noqa: E402
from feeder.fetchkit import Fetcher  # noqa: E402

FEEDS = [
    # NIST (seeds/2026-10-20_initial-sources.md)
    "https://www.nist.gov/news-events/news/rss.xml",
    "https://www.nist.gov/news-events/information%20technology/rss.xml",
    "https://www.nist.gov/blogs/cybersecurity-insights/rss.xml",
    "https://www.nist.gov/news-events/cybersecurity/rss.xml",
    "https://www.nist.gov/news-events/standards/rss.xml",
    # govinfo (same seed file)
    "https://www.govinfo.gov/rss/fr.xml",
    "https://www.govinfo.gov/rss/cfr.xml",
    "https://www.govinfo.gov/rss/lsa.xml",
    "https://www.govinfo.gov/rss/fr-bulkdata.xml",
    "https://www.govinfo.gov/rss/pai-bulkdata.xml",
]
SCHEMA_EXAMPLE_FEED = "https://www.nist.gov/blogs/cybersecurity-insights/rss.xml"
SCHEMA_EXAMPLE_ID = "nist-cybersecurity-insights"


def band_counts(records: list[dict]) -> dict:
    counts = {guard.CLEAN: 0, guard.REVIEW: 0, guard.QUARANTINE: 0, guard.BLOCK: 0}
    for rec in records:
        counts[rec["risk_level"]] += 1
    return counts


def rates(counts: dict) -> dict:
    total = sum(counts.values()) or 1
    return {k: round(v / total, 4) for k, v in counts.items()}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "live" / "titles.json"))
    ap.add_argument("--state-dir", default=str(ROOT / ".cache"))
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    fetcher = Fetcher(state_dir=Path(args.state_dir))
    feeds_out = []
    items_out = []
    example_methods = None
    example_item = None

    for url in FEEDS:
        result = fetcher.get(url, conditional=False)
        record = result.as_record()
        record["user_agent"] = USER_AGENT
        entry = {"fetch": record, "item_count": 0, "kept": 0, "parse_error": None, "kind": None}
        print(f"{url} -> {result.status} {result.error or 'ok'} {len(result.body or b'')} bytes")
        if result.ok:
            try:
                parsed = feed_adapter.parse_feed(result.body)
            except feed_adapter.FeedParseError as exc:
                entry["parse_error"] = str(exc)
                parsed = None
            if parsed is not None:
                entry["kind"] = parsed["kind"]
                entry["item_count"] = len(parsed["items"])
                for raw in parsed["items"]:
                    published = raw.published
                    if published is not None:
                        when = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        if when < cutoff:
                            continue
                    items_out.append({"feed_url": url, "title": raw.title, "link": raw.link,
                                      "guid": raw.guid, "published": published})
                    entry["kept"] += 1
                if url == SCHEMA_EXAMPLE_FEED and parsed["items"]:
                    first = parsed["items"][0]
                    item = items.build_item(SCHEMA_EXAMPLE_ID, url, first, retrieved_at=result.retrieved_at)
                    # The committed example never names a person: the feed's byline is dropped here.
                    item["authors"] = []
                    item["guard"] = {guard.FIELD_NAME: guard.scan_item_fields(first.title, first.summary)}
                    item["topics"] = ["cybersecurity", "standards/nist"]
                    example_item = item
                    example_methods = {
                        "id": SCHEMA_EXAMPLE_ID,
                        "routes": [{
                            "url": url,
                            "attempts": [{
                                "method": "conditional-get",
                                "tried_at": result.retrieved_at,
                                "result": "ok" if not result.truncated else "truncated",
                                "http_status": result.status,
                                "source_label": result.source_label,
                                "capture_url": None,
                                "media": [],
                                "note": f"parsed {len(parsed['items'])} items",
                            }],
                            "last_success": {"method": "conditional-get", "tried_at": result.retrieved_at,
                                             "source_label": result.source_label},
                        }],
                    }
        feeds_out.append(entry)

    scans = [guard.scan_text(it["title"]) for it in items_out]
    counts = band_counts(scans)
    for it, rec in zip(items_out, scans):
        it["guard_level"] = rec["risk_level"]
        it["guard_score"] = rec["total_score"]
    fixture = {
        "built_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": args.days,
        "cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_agent": USER_AGENT,
        "feeds": feeds_out,
        "items": items_out,
        "guard": {"counts": counts, "rates": rates(counts), "scored_field": "title"},
    }
    out = Path(args.out)
    jsonio.dump_json(out, fixture)
    fetcher.save()
    print(f"wrote {out} with {len(items_out)} titles from {sum(1 for f in feeds_out if f['fetch']['error'] is None)}/{len(FEEDS)} feeds")
    print(f"guard counts: {counts}")
    if example_methods is not None and example_item is not None:
        schema_dir = ROOT / "tests" / "fixtures" / "schema"
        jsonio.dump_json(schema_dir / "methods_example.json", example_methods)
        jsonio.dump_json(schema_dir / "item_example.json", example_item)
        print("wrote schema examples from the live NIST fetch")
    else:
        print("schema examples NOT written: the example feed did not parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
