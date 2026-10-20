"""Classification defaults for a new source: authority and tier, as code.

Every result is a default and carries `classification_review: "pending"`; a person confirms it
later. The rules are a table (`RULES`) evaluated top to bottom; the first row whose predicate
matches wins, and the default row always matches. One unit test exists per row.

Rows:
  government          host ends in .gov, .mil, .gov.<cc>, europa.eu, or a listed state government
                      domain                                              -> primary, canonical
  standards_or_court  a listed standards body or court domain             -> primary, canonical
  vendor_self         the route is on the publisher's own domain and the source is about that
                      publisher's own products or advisories              -> primary, vendor
  default             everything else                                      -> secondary, news
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

# Non-.gov domains run by US state governments. Extend here, one line each, with the state.
STATE_GOVERNMENT_DOMAINS: tuple[str, ...] = (
    "myflorida.com",   # Florida
    "flrules.org",     # Florida Administrative Code and Register
    "fldoe.org",       # Florida Department of Education
)

# Standards bodies and courts that are not on a government suffix, plus the government ones the
# brief named so the row is explicit about them.
STANDARDS_AND_COURT_DOMAINS: tuple[str, ...] = (
    "nist.gov",
    "iso.org",
    "iec.ch",
    "pcisecuritystandards.org",
    "ietf.org",
    "w3.org",
    "supremecourt.gov",
    "uscourts.gov",
)

_GOV_SUFFIX_RE = re.compile(r"(?:^|\.)(?:gov|mil)$|(?:^|\.)gov\.[a-z]{2}$|(?:^|\.)europa\.eu$")
_STATE_US_RE = re.compile(r"(?:^|\.)state\.[a-z]{2}\.us$")


@dataclass(frozen=True)
class Classification:
    authority: str
    tier: str
    rule: str
    classification_review: str = "pending"

    def as_dict(self) -> dict:
        return {
            "authority": self.authority,
            "tier": self.tier,
            "classification_review": self.classification_review,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class Candidate:
    """What the classifier knows about a source before it is written."""

    url: str
    publisher_homepage: str | None = None
    about_own_products: bool = False


def host_of(url: str) -> str:
    """Lowercase hostname without port or trailing dot; '' when the URL has none."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def _domain_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def registrable_domain(host: str) -> str:
    """A cheap approximation of the registrable domain: the last two labels, or the last three
    when the second-level label is a known country-code second level (co.uk, com.au, gov.uk)."""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if labels[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"} and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_government(host: str) -> bool:
    return bool(host) and (
        bool(_GOV_SUFFIX_RE.search(host))
        or bool(_STATE_US_RE.search(host))
        or _domain_matches(host, STATE_GOVERNMENT_DOMAINS)
    )


def is_standards_or_court(host: str) -> bool:
    return bool(host) and _domain_matches(host, STANDARDS_AND_COURT_DOMAINS)


def is_vendor_self(candidate: Candidate) -> bool:
    if not candidate.about_own_products or not candidate.publisher_homepage:
        return False
    route_host = host_of(candidate.url)
    publisher_host = host_of(candidate.publisher_homepage)
    if not route_host or not publisher_host:
        return False
    return registrable_domain(route_host) == registrable_domain(publisher_host)


RULES: tuple[tuple[str, Callable[[Candidate], bool], str, str], ...] = (
    ("government", lambda c: is_government(host_of(c.url)), "primary", "canonical"),
    ("standards_or_court", lambda c: is_standards_or_court(host_of(c.url)), "primary", "canonical"),
    ("vendor_self", is_vendor_self, "primary", "vendor"),
    ("default", lambda c: True, "secondary", "news"),
)


def classify(url: str, publisher_homepage: str | None = None,
             about_own_products: bool = False) -> Classification:
    """Default authority and tier for a route URL. Always `classification_review: pending`."""
    candidate = Candidate(url=url, publisher_homepage=publisher_homepage,
                          about_own_products=about_own_products)
    for name, predicate, authority, tier in RULES:
        if predicate(candidate):
            return Classification(authority=authority, tier=tier, rule=name)
    raise AssertionError("the default rule always matches")


__all__ = [
    "Candidate", "Classification", "RULES", "STANDARDS_AND_COURT_DOMAINS", "STATE_GOVERNMENT_DOMAINS",
    "classify", "host_of", "is_government", "is_standards_or_court", "is_vendor_self",
    "registrable_domain",
]
