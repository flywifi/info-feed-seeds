# FortiGuard RSS feeds, 2026-10-23

The 23 feeds listed at https://www.fortiguard.com/rss-feeds, read on 2026-10-23. Every feed was fetched
twice on 2026-10-25 with the named User-Agent through the feeder's own fetcher; the status column is from
the second fetch and the proof records under `sources/<id>/methods.json` carry both. "Bucket" says whether a
person reads the lines (readable, an enabled source) or a program does (machine, stored with
`enabled: false`). "Category" is the source's category/subcategory in `schema/categories.json`,
as its manifest carries it; machine-data sources are shown only by a profile that names that category.

robots.txt: www.fortiguard.com and fortiguard.fortinet.com both answer 200 with `User-agent: *`,
`Crawl-delay: 2`, `Disallow: /*?*` and `Disallow: /threat-research/data`; filestore.fortinet.com and
support.fortinet.com answer 404 (no rules). No feed URL carries a query string, and every listing page
fetched below is allowed. The item version pages carry a `?version=` query string, which
fortiguard.fortinet.com disallows, so no adapter may fetch them under the rules.

Homepages: the psirt, outbreak-alert and threat-signal-report pages were derived from the item links and
answered 200 when fetched once on 2026-10-25. The firmware feed's item link, a support portal path, refused
its redirect, so that source's homepage is https://support.fortinet.com/ (200).

| id | feed | url | bucket | category | status |
|---|---|---|---|---|---|
| fortiguard-psirt | FortiGuard PSIRT advisories | https://filestore.fortinet.com/fortiguard/rss/ir.xml | readable | vulnerabilities/vendor-advisories | 200, 50 items, newest 2026-10-21 |
| fortiguard-outbreak-alerts | FortiGuard outbreak alerts | https://filestore.fortinet.com/fortiguard/rss/outbreakalert.xml | readable | attacks/exploited-now | 200, 20 items, newest 2026-10-23 |
| fortiguard-threat-signal | FortiGuard threat signal reports | https://filestore.fortinet.com/fortiguard/rss/threatsignal.xml | readable | attacks/threat-research | 200, 10 items, newest 2026-10-24 |
| fortinet-firmware | Fortinet firmware updates | https://support.fortinet.com/rss/firmware.xml | readable | releases/firmware | 200, 77 items, newest 2026-10-23 |
| fortiguard-endpoint-vulnerability | FortiGuard endpoint vulnerability updates | https://filestore.fortinet.com/fortiguard/rss/epvuln.xml | readable | vulnerabilities/detections | 200, 10 items, newest 2026-10-24 |
| fortiguard-ips | FortiGuard intrusion prevention updates | https://filestore.fortinet.com/fortiguard/rss/ipsdatabase.xml | readable | vulnerabilities/detections | 200, 10 items, newest 2026-10-24 |
| fortiguard-ot-ips | FortiGuard OT intrusion prevention updates | https://filestore.fortinet.com/fortiguard/rss/otips.xml | readable | vulnerabilities/detections | 200, 10 items, newest 2026-10-24 |
| fortiguard-ot-security | FortiGuard operational technology security services | https://filestore.fortinet.com/fortiguard/rss/indsec.xml | readable | vulnerabilities/detections | 200, 10 items, newest 2026-10-21 |
| fortiguard-virtual-patching | FortiGuard virtual patching updates | https://filestore.fortinet.com/fortiguard/rss/fmwp.xml | readable | vulnerabilities/detections | 200, 10 items, newest 2026-10-24 |
| fortiguard-application-control | FortiGuard application control updates | https://filestore.fortinet.com/fortiguard/rss/appcontrol.xml | readable | vulnerabilities/detections | 200, 10 items, newest 2026-10-23 |
| fortiguard-antivirus | FortiGuard antivirus definition updates | https://filestore.fortinet.com/fortiguard/rss/antivirusdatabase.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-26 |
| fortiguard-antispam-checksum | FortiGuard antispam checksum updates | https://filestore.fortinet.com/fortiguard/rss/aschecksum.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-26, title-only items without links |
| fortiguard-antispam-uri | FortiGuard antispam URI updates | https://filestore.fortinet.com/fortiguard/rss/asuri.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-26, title-only items without links |
| fortiguard-antispam-ip | FortiGuard antispam IP updates | https://filestore.fortinet.com/fortiguard/rss/asip.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-26, title-only items without links |
| fortiguard-web-filtering | FortiGuard web filtering updates | https://filestore.fortinet.com/fortiguard/rss/webfiltering.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-26 |
| fortiguard-web-security | FortiWeb application security updates | https://filestore.fortinet.com/fortiguard/rss/websecurity.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-22 |
| fortiguard-mobile-antivirus | FortiGuard mobile antivirus updates | https://filestore.fortinet.com/fortiguard/rss/mobile.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-26 |
| fortiguard-ip-reputation | FortiGuard IP reputation database updates | https://filestore.fortinet.com/fortiguard/rss/irdb.xml | machine | machine-data/name-lists | 200, 8 items, newest 2026-10-15 |
| fortiguard-botnet-domains | FortiGuard botnet domain reputation updates | https://filestore.fortinet.com/fortiguard/rss/botnetdom.xml | machine | machine-data/name-lists | 200, 10 items, newest 2026-10-24 |
| fortiguard-internet-services | FortiGuard internet services database updates | https://filestore.fortinet.com/fortiguard/rss/isdb.xml | machine | machine-data/name-lists | 200, 10 items, newest 2026-10-24 |
| fortiguard-iot-applications | FortiGuard IoT application service updates | https://filestore.fortinet.com/fortiguard/rss/iotapp.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-24 |
| fortiguard-ot-applications | FortiGuard OT application service updates | https://filestore.fortinet.com/fortiguard/rss/otapp.xml | machine | machine-data/signature-versions | 200, 10 items, newest 2026-10-24 |
| fortiguard-dast | FortiGuard dynamic application security testing updates | https://filestore.fortinet.com/fortiguard/rss/fortidast.xml | machine | machine-data/signature-versions | 200, 4 items, newest 2025-01-15, stale |

## Update listing pages

The signature and reputation feeds title each item with a version number (for example `37.319`) and put
the readable part in the summary: the signatures newly added or modified, with CVE ids and severities.
Each item links to a version page under https://fortiguard.fortinet.com/updates/ with a `?version=` query
string, and the listing page without the version is recorded as a second route (`adapter: page`) on the
16 sources whose items carry such links, with a reachability record. Every listing page answered 200
with a body of about 19.7 KB, the same size for every service, so the listing content is not in the
page body; nothing in the repository reads the signature lists yet, and the version pages are off limits
under that host's robots.txt (see above). The three antispam feeds carry no item links and no listing
page.

| id | listing page | status |
|---|---|---|
| fortiguard-endpoint-vulnerability | https://fortiguard.fortinet.com/updates/epvuln | 200, 19733 bytes, robots allowed |
| fortiguard-ips | https://fortiguard.fortinet.com/updates/ips | 200, 19730 bytes, robots allowed |
| fortiguard-ot-ips | https://fortiguard.fortinet.com/updates/otips | 200, 19732 bytes, robots allowed |
| fortiguard-ot-security | https://fortiguard.fortinet.com/updates/ots | 200, 19730 bytes, robots allowed |
| fortiguard-virtual-patching | https://fortiguard.fortinet.com/updates/virtual-patching | 200, 19743 bytes, robots allowed |
| fortiguard-application-control | https://fortiguard.fortinet.com/updates/app | 200, 19730 bytes, robots allowed |
| fortiguard-antivirus | https://fortiguard.fortinet.com/updates/antivirus | 200, 19736 bytes, robots allowed |
| fortiguard-web-filtering | https://fortiguard.fortinet.com/updates/webfiltering | 200, 19739 bytes, robots allowed |
| fortiguard-web-security | https://fortiguard.fortinet.com/updates/websecurity | 200, 19738 bytes, robots allowed |
| fortiguard-mobile-antivirus | https://fortiguard.fortinet.com/updates/mobile | 200, 19733 bytes, robots allowed |
| fortiguard-ip-reputation | https://fortiguard.fortinet.com/updates/irdb | 200, 19731 bytes, robots allowed |
| fortiguard-botnet-domains | https://fortiguard.fortinet.com/updates/botnetdom | 200, 19736 bytes, robots allowed |
| fortiguard-internet-services | https://fortiguard.fortinet.com/updates/isdb | 200, 19731 bytes, robots allowed |
| fortiguard-iot-applications | https://fortiguard.fortinet.com/updates/iotapp | 200, 19733 bytes, robots allowed |
| fortiguard-ot-applications | https://fortiguard.fortinet.com/updates/otapp | 200, 19732 bytes, robots allowed |
| fortiguard-dast | https://fortiguard.fortinet.com/updates/dynamic-application-security-testing | 200, 19763 bytes, robots allowed |

## Notes

- The firmware feed (support.fortinet.com) has 77 items with product and version titles such as
  `FortiFoneAndroid 8.0.2` and links to the download page.
- `fortidast.xml` last published on 2025-01-15; it is stored off and `expected_update_period_days` is 90.
- Six enabled feeds (endpoint vulnerability, IPS, OT IPS, OT security, virtual patching, application
  control) title each item with a version number; the summary carries the readable content, the
  signatures added or modified with CVE ids and severities. The digest shows the title, the link and up
  to 300 characters of the summary; nothing in the repository extracts the CVE ids or severities yet.
- Six feeds (antivirus, the three antispam feeds, mobile antivirus, web filtering) stamp their items 3.5
  to 7 hours ahead of the fetch time in UTC, so the newest item of a fetch made on 2026-10-25 reads
  2026-10-26. The values are recorded as the feed gives them.
