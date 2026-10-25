# FortiGuard RSS feeds, 2026-10-23

The 23 feeds listed at https://www.fortiguard.com/rss-feeds, read on 2026-10-23. Every feed was fetched
twice on 2026-10-25 with the named User-Agent through the feeder's own fetcher; the status column is from
the second fetch and the proof records under `sources/<id>/methods.json` carry both. "Bucket" is the
reader decision for job 2: readable feeds are enabled sources, machine feeds are stored with
`enabled: false` until profiles can switch a category off. "Category" is the intended reader category
for the categories registry; manifests carry topics only until that registry exists.

robots.txt: www.fortiguard.com answers 200 with `Crawl-delay: 2`, `Disallow: /*?*` and
`Disallow: /threat-research/data`; filestore.fortinet.com and support.fortinet.com answer 404 (no rules);
fortiguard.fortinet.com allowed every update page fetched below. No feed URL carries a query string.

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
Each item links to a version page under https://fortiguard.fortinet.com/updates/, and the listing page
without the version is recorded as a second route (`adapter: page`) on those sources with a reachability
record. Every listing page answered 200 with a body of about 19.7 KB, the same size for every service,
so the server sends a script-driven shell; a page adapter will need the version pages or a different
route to read the signature lists. The three antispam feeds carry no item links and no listing page.

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
- Readable feeds with version-number titles (endpoint vulnerability, IPS, OT IPS, OT security, virtual
  patching, application control) stay enabled on the maintainer's decision; the digest shows the version
  as the title and the summary carries the signature list.
