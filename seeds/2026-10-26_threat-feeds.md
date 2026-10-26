<!-- last_reviewed: 2026-10-26 | owner: maintainer -->
# Threat and advisory feeds requested on 2026-10-26

Fetched from a cloud session with the named User-Agent on 2026-10-26. Only a live 200 that parses proves a
source, so only ervik-threat-feed is in `sources/`; the rest wait for a live proof.

| feed | url | result | items | intended source category | topics |
|---|---|---|---|---|---|
| Ervik threat feed | https://www.ervik.as/rss.xml | 200, added as `ervik-threat-feed` | 60 | attacks/threat-research | cybersecurity/ransomware, cybersecurity/vulnerabilities, cybersecurity/incidents |
| Pulse underground intelligence (English) | https://pulse.kalir.io/en/feed.xml | 200, valid RSS with no items | 0 | attacks/threat-research | cybersecurity/incidents |
| deepdarkCTI repository | https://github.com/fastfire/deepdarkCTI | 403 on commits.atom and releases.atom | n/a | attacks/threat-research | cybersecurity/incidents |
| CISA ICS advisories | https://www.cisa.gov/cybersecurity-advisories/ics-advisories.xml | 403 bot wall; a user-saved copy parsed | 30 | vulnerabilities/vendor-advisories | cybersecurity/ot-ics, cybersecurity/vulnerabilities |
| CISA ICS medical advisories | https://www.cisa.gov/cybersecurity-advisories/ics-medical-advisories.xml | 403 bot wall; a user-saved copy parsed | 30 | vulnerabilities/vendor-advisories | healthcare/health-it, cybersecurity/ot-ics |
| CISA news | https://www.cisa.gov/news.xml | 403 bot wall; a user-saved copy parsed | 10 | guidance/security-guidance | cybersecurity, government/federal |

Ervik's feed carries four item types, placed per item by content category: "Ransomware: victim (group)"
titles go to attacks/breaches, "Active Zero-Day:" titles to attacks/exploited-now, "CVE-" titles to
vulnerabilities/scoring, and the daily briefing headlines by their keywords. The ransomware items all
link to https://www.ervik.as/ransomware, so their titles, not their links, tell them apart.

deepdarkCTI is a list of dark web, Telegram and forum addresses in Markdown, not a feed; its commit
feed is the route to watch for changes. The user-saved CISA copies are 2026-10-26 captures; their
newest items are dated 2026-10-22 (ICS), 2026-10-21 (ICS medical) and 2026-10-21 (news).
