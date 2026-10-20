# info-feed-seeds

Seed sources for a small RSS, Atom, OPML and API feeder that produces daily, topic-tagged digests
for several projects: technology and regulation, education, finance, and others as they are added.
The digests feed social posts, article ideas and source lists.

This repository holds only public URLs and neutral topic tags. It never holds credentials, private
notes or anything that identifies a client or a campaign.

## Layout

- `seeds/`: seed files. Plain URL lists, OPML files and the triaged source table. These are imports;
  the registry is the truth once it exists.
- `registry/` (planned): `sources.json`, one entry per source with id, url, kind (rss, atom, opml,
  sitemap, json-api, html-page, bulk-xml), publisher, topics, cadence and parser hint.
- `profiles/` (planned): one profile per project with its topics, include and exclude keywords and
  daily item cap.
- `digests/` (planned): `digests/<profile>/YYYY-MM-DD.md`, written by the scheduled poll.
- `feeder/` (planned): the stdlib Python poller, deduper and digest writer.

## Rules

- Public URLs and neutral tags only.
- Federal government content is public domain; everything else is link and attribution only.
- No em dashes in anything written here.
