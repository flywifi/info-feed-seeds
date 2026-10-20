# Initial seed sources, 2026-10-20

Triage of the first batch. "Kind" is the parser the feeder will need. "Projects" is a first guess at
which profiles want the source.

## govinfo (U.S. Government Publishing Office)

| URL | Kind | What it is | Projects |
|---|---|---|---|
| https://www.govinfo.gov/rss/fr.xml | rss | Federal Register, daily issue | regulation, technology, finance, education |
| https://www.govinfo.gov/rss/cfr.xml | rss | Code of Federal Regulations updates | regulation |
| https://www.govinfo.gov/rss/lsa.xml | rss | List of CFR Sections Affected | regulation |
| https://www.govinfo.gov/rss/fr-bulkdata.xml | rss | Federal Register bulk data notices | regulation (low volume) |
| https://www.govinfo.gov/rss/pai-bulkdata.xml | rss | Public and Private Laws bulk data notices | regulation (low volume) |
| https://github.com/usgpo/rss/tree/master/opml | opml | OPML files listing every govinfo feed; import and pick | registry import |
| https://github.com/usgpo/rss | reference | Repo documenting the govinfo feeds | documentation |
| https://www.govinfo.gov/feeds | reference | Human index of the feeds above | documentation |
| https://github.com/usgpo/sitemap | sitemap | Sitemaps for discovery and bulk, not feeds; heavy | skip until needed |

## HHS and CDC content syndication

These five URLs are one system: the HHS Syndication API. The storefront, badge snippet and the CDC
media library are its front ends; the hhs.gov page is documentation.

| URL | Kind | What it is | Projects |
|---|---|---|---|
| https://api.digitalmedia.hhs.gov/ | json-api | HHS Syndication API (JSON and XML endpoints for HHS and CDC content) | education, health |
| https://www.hhs.gov/digital/resources/syndication/index.html | reference | Documentation for the API | documentation |
| https://digitalmedia.hhs.gov/storefront/ | reference | Web front end for browsing syndicated content | documentation |
| https://digitalmedia.hhs.gov/syndicateThis/badgeSnippet | reference | Embed badge generator, not an input | skip |
| https://tools.cdc.gov/medialibrary/index.aspx#/additionalresources | reference | CDC Public Health Media Library, front end of the same API | documentation |

## NIST (checked 2026-10-19)

| URL | Kind | What it is | Projects |
|---|---|---|---|
| https://www.nist.gov/coo/nist-rss-feeds | reference | Directory of all NIST feeds | documentation |
| https://www.nist.gov/news-events/news/rss.xml | rss | NIST news, all topics | technology |
| https://www.nist.gov/news-events/information%20technology/rss.xml | rss | Information Technology topic; carries the cybersecurity news items | technology, regulation |
| https://www.nist.gov/blogs/cybersecurity-insights/rss.xml | rss | Cybersecurity Insights blog | technology, regulation |
| https://www.nist.gov/news-events/cybersecurity/rss.xml | rss | Cybersecurity topic feed; live but empty on 2026-10-19 | watch |
| https://www.nist.gov/news-events/standards/rss.xml | rss | Standards topic | technology, regulation |

## Education sector security

| URL | Kind | What it is | Projects |
|---|---|---|---|
| https://edsecledger.com/rss.xml | rss | EdSecLedger, breach and regulatory intelligence for schools, colleges and EdTech | education |

## Notes

- nist.gov returns 403 to requests without a User-Agent header. The feeder needs a named one.
- govinfo also has an API (api.govinfo.gov, free key) and bulk data; neither is needed for a daily digest.
