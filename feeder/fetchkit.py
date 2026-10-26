"""Polite, cached, robots-aware fetching for every live request the feeder makes.

Ported from the dbz `cross-mapping/engine/fetchkit.py` and reshaped for this repository:

  RateGovernor  per-host pacing (2 s default, per-host overrides, robots Crawl-delay), Retry-After
                honoured and decayed after three clean responses, a circuit breaker (four consecutive
                403/429/503 or network errors trips a 15 minute cooldown), a pacing budget (total
                seconds a run may spend waiting) and persisted learned limits.
  FetchCache    per-URL ETag and Last-Modified validators plus a sha256 unchanged check.
  RobotsCache   robots.txt fetched with the named User-Agent and parsed with
                RobotFileParser.parse (never read()); 401 or 403 is unknown, 5xx or a network error
                is disallowed for the run after one retry, HTML served as robots.txt is no rules.
  Fetcher       the facade: `get(url)` returns a FetchResult carrying status, body, final URL, every
                redirect hop, the `truncated` flag, the source label (live, cache or archive) and a
                trail of what was tried. Redirects from https to http are refused.

State is written atomically through `feeder.jsonio`. The archive prong is off by default
(`allow_wayback=False`); when it runs, the result is labelled archive and the capture URL is
recorded only after that URL was actually fetched.
"""
from __future__ import annotations

import io
import json
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

from . import ROOT, USER_AGENT, jsonio

DEFAULT_STATE_DIR = ROOT / ".cache"
DEFAULT_MIN_INTERVAL = 2.0          # seconds between requests to one host
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
DEFAULT_PACING_BUDGET = 10 * 60.0   # seconds a run may spend waiting, in total
DEFAULT_BODY_CAP = 5 * 1024 * 1024  # bytes kept from any one body
DEFAULT_HOST_INTERVALS = {"web.archive.org": 6.0, "index.commoncrawl.org": 10.0}
BREAKER_FAILS = 4
BREAKER_COOLDOWN = 15 * 60.0
BREAKER_STATUSES = frozenset({403, 429, 503})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
REFUSAL_STATUSES = frozenset({401, 451})
DECAY_CLEAN_RESPONSES = 3
RETRY_AFTER_CAP = 300.0
ROBOTS_TIMEOUT = 10.0
WAYBACK_AVAILABLE = "https://archive.org/wayback/available?url="
BOT_WALL_FINGERPRINTS = (
    "just a moment", "cf-mitigated", "attention required", "captcha", "cf-chl",
    "request unsuccessful. incapsula", "perimeterx", "px-captcha", "datadome",
    "are you a human", "bot detection", "access denied</h1>", "ray id",
)
_CHUNK = 64 * 1024


class HostCooldown(Exception):
    """The host's circuit breaker is active; skip it, do not hammer."""


class BudgetExhausted(Exception):
    """The run's pacing or request budget is spent."""


class RedirectRefused(Exception):
    """A redirect was refused: https to http, a scheme other than http or https, or (for a request
    pinned to an origin) another host or port."""


_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> tuple[str, int | None] | None:
    """(host, port) with the scheme's default port read as None; None for a URL without a usable host."""
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname.lower(), None if port == _DEFAULT_PORTS.get(parts.scheme.lower()) else port


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def utc_now_z(clock: Callable[[], float] = time.time) -> str:
    return datetime.fromtimestamp(clock(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- redirects

class HopRecorder(urllib.request.HTTPRedirectHandler):
    """Records every redirect hop and refuses a downgrade from https to http and any scheme but http
    and https. A request carrying `pinned_origin` (host, port) is also refused a hop to another origin,
    before that hop is requested."""

    def __init__(self) -> None:
        super().__init__()
        self.hops: list[dict] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops.append({"from": req.full_url, "to": newurl, "status": code})
        if req.full_url.lower().startswith("https://") and newurl.lower().startswith("http://"):
            raise RedirectRefused(f"refused https to http redirect: {req.full_url} -> {newurl}")
        if urllib.parse.urlsplit(newurl).scheme.lower() not in _DEFAULT_PORTS:
            raise RedirectRefused(f"refused redirect to another scheme: {req.full_url} -> {newurl}")
        pinned = getattr(req, "pinned_origin", None)
        if pinned is not None and origin_of(newurl) != pinned:
            raise RedirectRefused(f"refused redirect to another host: {req.full_url} -> {newurl}")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and pinned is not None:
            new.pinned_origin = pinned
        return new


def default_opener(req: urllib.request.Request, timeout: float):
    """Open a request with the hop recorder; the hops ride on the response or the HTTPError."""
    recorder = HopRecorder()
    opener = urllib.request.build_opener(recorder)
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        exc.hops = list(recorder.hops)
        raise
    resp.hops = list(recorder.hops)
    return resp


# --------------------------------------------------------------------------- bodies

def read_body(resp, cap: int) -> tuple[bytes, bool]:
    """Stream the body up to `cap` bytes. Returns (bytes, truncated). Handles gzip and deflate
    transparently, including a truncated gzip stream, and never raises on a mislabelled encoding."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        chunk = resp.read(_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            truncated = True
            break
    body = b"".join(chunks)[:cap]
    headers = getattr(resp, "headers", None) or {}
    encoding = (headers.get("Content-Encoding") or "").lower()
    if encoding in ("gzip", "x-gzip", "deflate"):
        wbits = 16 + zlib.MAX_WBITS if encoding != "deflate" else zlib.MAX_WBITS
        try:
            body = zlib.decompressobj(wbits).decompress(body)
        except zlib.error:
            try:
                body = zlib.decompressobj(-zlib.MAX_WBITS).decompress(body)
            except zlib.error:
                pass  # mislabelled encoding: keep the raw bytes
        if len(body) > cap:
            body = body[:cap]
            truncated = True
    return body, truncated


def looks_like_bot_wall(body: bytes) -> bool:
    head = body[:16384].decode("utf-8", errors="replace").lower()
    return any(fp in head for fp in BOT_WALL_FINGERPRINTS)


def _retry_after_seconds(value: str | None, clock: Callable[[], float]) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - clock())


# --------------------------------------------------------------------------- pacing

class RateGovernor:
    """Per-host pacing, Retry-After with decay, circuit breaker, pacing budget. Persisted."""

    def __init__(self, state_path: Path | None, *,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 host_intervals: dict[str, float] | None = None,
                 pacing_budget: float = DEFAULT_PACING_BUDGET,
                 request_budget: int | None = None,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep,
                 jitter: float = 0.4) -> None:
        self.state_path = state_path
        self.min_interval = float(min_interval)
        self.host_intervals = dict(DEFAULT_HOST_INTERVALS)
        self.host_intervals.update(host_intervals or {})
        self.pacing_budget = float(pacing_budget)
        self.pacing_spent = 0.0
        self.request_budget = request_budget
        self.requests_made = 0
        self._clock = clock
        self._sleep = sleeper
        self._jitter = jitter
        self.hosts: dict[str, dict] = {}
        if state_path is not None and state_path.exists():
            try:
                self.hosts = jsonio.load_json(state_path).get("hosts", {})
            except (OSError, ValueError):
                self.hosts = {}

    def base_interval(self, host: str) -> float:
        return max(self.min_interval, self.host_intervals.get(host, 0.0))

    def _entry(self, host: str) -> dict:
        entry = self.hosts.get(host)
        if entry is None:
            entry = {"last_ts": 0.0, "interval": self.base_interval(host), "floor": 0.0,
                     "fails": 0, "breaker_until": 0.0, "clean_streak": 0}
            self.hosts[host] = entry
        else:
            entry.setdefault("floor", 0.0)
            entry.setdefault("clean_streak", 0)
            entry["interval"] = max(float(entry.get("interval", 0.0)), self.base_interval(host))
        return entry

    def set_floor(self, host: str, seconds: float | None) -> None:
        """Raise a host's floor interval, for example from a robots.txt Crawl-delay."""
        if seconds is None:
            return
        entry = self._entry(host)
        entry["floor"] = max(float(entry.get("floor", 0.0)), float(seconds))
        entry["interval"] = max(entry["interval"], entry["floor"])

    def effective_interval(self, host: str) -> float:
        entry = self._entry(host)
        return max(entry["interval"], self.base_interval(host), entry.get("floor", 0.0))

    def pause(self, seconds: float) -> None:
        """Sleep against the pacing budget. Raises BudgetExhausted instead of overspending."""
        if seconds <= 0:
            return
        if self.pacing_spent + seconds > self.pacing_budget:
            raise BudgetExhausted(
                f"pacing budget of {self.pacing_budget:.0f}s would be exceeded by a {seconds:.1f}s wait")
        self._sleep(seconds)
        self.pacing_spent += seconds

    def wait(self, host: str) -> None:
        """Block until this host may be contacted. Raises HostCooldown or BudgetExhausted."""
        now = self._clock()
        entry = self._entry(host)
        if now < float(entry.get("breaker_until", 0.0)):
            raise HostCooldown(f"{host} cooling down until {entry['breaker_until']:.0f}")
        if self.request_budget is not None and self.requests_made >= self.request_budget:
            raise BudgetExhausted("request budget exhausted for this run")
        wait_for = float(entry["last_ts"]) + self.effective_interval(host) + random.uniform(0, self._jitter) - now
        if wait_for > 0:
            self.pause(wait_for)
        entry["last_ts"] = self._clock()
        self.requests_made += 1

    def note(self, host: str, status: int, headers=None) -> None:
        """Record a response. status 0 means a network error."""
        entry = self._entry(host)
        headers = headers or {}
        retry_after = _retry_after_seconds(headers.get("Retry-After") or headers.get("retry-after"),
                                           self._clock)
        if retry_after is not None:
            entry["interval"] = max(entry["interval"], min(retry_after, RETRY_AFTER_CAP))
        reset = headers.get("RateLimit-Reset") or headers.get("X-RateLimit-Reset")
        if reset and status in BREAKER_STATUSES:
            try:
                until = float(reset)
                if until > 1e9:
                    entry["breaker_until"] = max(entry["breaker_until"], until)
                else:
                    entry["breaker_until"] = max(entry["breaker_until"], self._clock() + min(until, 3600))
            except ValueError:
                pass
        if status in BREAKER_STATUSES or status == 0:
            entry["fails"] = int(entry.get("fails", 0)) + 1
            entry["clean_streak"] = 0
            if entry["fails"] >= BREAKER_FAILS:
                entry["breaker_until"] = max(float(entry.get("breaker_until", 0.0)),
                                             self._clock() + BREAKER_COOLDOWN)
        else:
            entry["fails"] = 0
            entry["clean_streak"] = int(entry.get("clean_streak", 0)) + 1
            if entry["clean_streak"] >= DECAY_CLEAN_RESPONSES and retry_after is None:
                entry["interval"] = max(self.base_interval(host), float(entry.get("floor", 0.0)))

    def save(self) -> None:
        if self.state_path is None:
            return
        jsonio.dump_json(self.state_path, {"hosts": self.hosts})


# --------------------------------------------------------------------------- conditional GET

class FetchCache:
    """Conditional-GET validators plus sha256 unchanged detection, persisted per URL."""

    def __init__(self, state_path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.state_path = state_path
        self._clock = clock
        self.hits = 0
        self.misses = 0
        self.urls: dict[str, dict] = {}
        if state_path is not None and state_path.exists():
            try:
                self.urls = jsonio.load_json(state_path).get("urls", {})
            except (OSError, ValueError):
                self.urls = {}

    def validators(self, url: str) -> dict:
        entry = self.urls.get(url, {})
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        return headers

    def note_response(self, url: str, status: int, headers, body: bytes | None) -> str:
        """Returns 'unchanged' (304 or identical sha256) or 'changed'."""
        import hashlib
        headers = headers or {}
        if status == 304:
            self.hits += 1
            self.urls.setdefault(url, {})["checked_at"] = utc_now_z(self._clock)
            return "unchanged"
        sha = hashlib.sha256(body or b"").hexdigest()
        prev = self.urls.get(url, {}).get("sha256")
        self.urls[url] = {
            "etag": headers.get("ETag", "") or headers.get("etag", ""),
            "last_modified": headers.get("Last-Modified", "") or headers.get("last-modified", ""),
            "sha256": sha,
            "checked_at": utc_now_z(self._clock),
        }
        if prev == sha:
            self.hits += 1
            return "unchanged"
        self.misses += 1
        return "changed"

    def cached_sha(self, url: str) -> str | None:
        return self.urls.get(url, {}).get("sha256")

    def save(self) -> None:
        if self.state_path is None:
            return
        jsonio.dump_json(self.state_path, {"urls": self.urls})


# --------------------------------------------------------------------------- robots.txt

ROBOTS_ALLOWED, ROBOTS_DISALLOWED, ROBOTS_UNKNOWN, ROBOTS_NO_RULES = (
    "allowed", "disallowed", "unknown", "no_rules")


def _is_html(body: bytes, content_type: str) -> bool:
    if "html" in content_type.lower():
        return True
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html", b"<head", b"<body"))


class RobotsCache:
    """One robots.txt fetch per host per run, parsed with RobotFileParser.parse."""

    def __init__(self, opener: Callable, user_agent: str, governor: RateGovernor,
                 timeout: float = ROBOTS_TIMEOUT) -> None:
        self._opener = opener
        self._ua = user_agent
        self._governor = governor
        self._timeout = timeout
        self.entries: dict[str, dict] = {}

    def _fetch(self, origin: str, host: str) -> tuple[int, bytes, str]:
        req = urllib.request.Request(origin + "/robots.txt",
                                     headers={"User-Agent": self._ua, "Accept": "text/plain, */*;q=0.1"})
        self._governor.wait(host)
        try:
            resp = self._opener(req, self._timeout)
        except urllib.error.HTTPError as exc:
            self._governor.note(host, exc.code, dict(exc.headers or {}))
            try:
                body, _ = read_body(exc, 65536)
            except Exception:  # noqa: BLE001
                body = b""
            return exc.code, body, (exc.headers or {}).get("Content-Type", "") if exc.headers else ""
        headers = dict(resp.headers)
        body, _ = read_body(resp, 65536)
        self._governor.note(host, resp.status, headers)
        return resp.status, body, headers.get("Content-Type", "")

    def _load(self, origin: str, host: str) -> dict:
        entry = {"status": ROBOTS_UNKNOWN, "parser": None, "crawl_delay": None, "http_status": None,
                 "note": ""}
        attempts = 0
        while True:
            attempts += 1
            try:
                status, body, ctype = self._fetch(origin, host)
            except (HostCooldown, BudgetExhausted):
                raise  # the caller reports cooldown or budget; nothing is cached for the host
            except Exception as exc:  # noqa: BLE001  network errors of every kind
                self._governor.note(host, 0, {})
                if attempts <= 1:
                    continue
                entry.update(status=ROBOTS_DISALLOWED, note=f"robots network error: {type(exc).__name__}")
                return entry
            entry["http_status"] = status
            if status >= 500:
                if attempts <= 1:
                    continue
                entry.update(status=ROBOTS_DISALLOWED, note=f"robots.txt returned {status}")
                return entry
            if status in (401, 403):
                entry.update(status=ROBOTS_UNKNOWN, note=f"robots.txt returned {status}")
                return entry
            if status >= 400:
                entry.update(status=ROBOTS_NO_RULES, note=f"robots.txt returned {status}")
                return entry
            if _is_html(body, ctype):
                entry.update(status=ROBOTS_NO_RULES, note="html served as robots.txt")
                return entry
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            delay = parser.crawl_delay(self._ua)
            entry.update(status=ROBOTS_ALLOWED, parser=parser,
                         crawl_delay=float(delay) if delay is not None else None)
            return entry

    def check(self, url: str) -> dict:
        """Return {"status", "crawl_delay", "http_status", "note"} for the URL's host. Raises
        HostCooldown or BudgetExhausted when the robots fetch itself could not be paced."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            return {"status": ROBOTS_NO_RULES, "crawl_delay": None, "http_status": None, "note": "no host"}
        origin = f"{parts.scheme}://{parts.netloc}"
        key = f"{parts.scheme}://{host}"
        entry = self.entries.get(key)
        if entry is None:
            entry = self._load(origin, host)
            self.entries[key] = entry
        status = entry["status"]
        if status == ROBOTS_ALLOWED and entry["parser"] is not None:
            status = ROBOTS_ALLOWED if entry["parser"].can_fetch(self._ua, url) else ROBOTS_DISALLOWED
        return {"status": status, "crawl_delay": entry["crawl_delay"],
                "http_status": entry["http_status"], "note": entry["note"]}


# --------------------------------------------------------------------------- results

@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes | None
    headers: dict = field(default_factory=dict)
    truncated: bool = False
    hops: list = field(default_factory=list)
    source_label: str = "live"
    cache_verdict: str | None = None
    trail: list = field(default_factory=list)
    retrieved_at: str = ""
    error: str | None = None
    robots: str = ROBOTS_NO_RULES
    capture_url: str | None = None
    snapshot_ts: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.body is not None

    def as_record(self) -> dict:
        """The citation-grade capture record: what was fetched, from where, when."""
        return {
            "url": self.url, "final_url": self.final_url, "status": self.status,
            "retrieved_at": self.retrieved_at, "source_label": self.source_label,
            "truncated": self.truncated, "hops": list(self.hops), "capture_url": self.capture_url,
            "error": self.error, "robots": self.robots, "attempts": self.attempts,
        }


# --------------------------------------------------------------------------- facade

class Fetcher:
    def __init__(self, *, state_dir: Path | None = DEFAULT_STATE_DIR,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 host_intervals: dict[str, float] | None = None,
                 pacing_budget: float = DEFAULT_PACING_BUDGET,
                 request_budget: int | None = None,
                 body_cap: int = DEFAULT_BODY_CAP,
                 timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES,
                 allow_wayback: bool = False,
                 user_agent: str = USER_AGENT,
                 opener: Callable = default_opener,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep,
                 jitter: float = 0.4) -> None:
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.body_cap = int(body_cap)
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.allow_wayback = allow_wayback
        self.user_agent = user_agent
        self._opener = opener
        self._clock = clock
        host_state = self.state_dir / "host_limits.json" if self.state_dir else None
        url_state = self.state_dir / "fetch_state.json" if self.state_dir else None
        self.governor = RateGovernor(host_state, min_interval=min_interval, host_intervals=host_intervals,
                                     pacing_budget=pacing_budget, request_budget=request_budget,
                                     clock=clock, sleeper=sleeper, jitter=jitter)
        self.cache = FetchCache(url_state, clock=clock)
        self.robots = RobotsCache(opener, user_agent, self.governor)

    # -- helpers

    def _headers(self, url: str, conditional: bool, extra: dict | None) -> dict:
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate",
                   "Accept": "application/rss+xml, application/atom+xml, application/feed+json, "
                             "application/json, application/xml, text/xml, text/html;q=0.8, */*;q=0.5"}
        if conditional:
            headers.update(self.cache.validators(url))
        headers.update(extra or {})
        return headers

    def _result(self, url: str, **kw) -> FetchResult:
        kw.setdefault("final_url", url)
        kw.setdefault("status", 0)
        kw.setdefault("body", None)
        kw.setdefault("retrieved_at", utc_now_z(self._clock))
        return FetchResult(url=url, **kw)

    # -- the live prong

    def get(self, url: str, *, conditional: bool = True, extra_headers: dict | None = None,
            respect_robots: bool = True, same_origin: bool = False) -> FetchResult:
        """`same_origin` refuses any redirect off the URL's own host and port before it is followed."""
        scheme = url.partition(":")[0].lower() if ":" in url else ""
        if scheme not in ("http", "https"):
            # urllib's default opener would read file:, ftp: and data: URLs; a feed never needs them.
            return self._result(url, trail=["scheme-refused"], robots=ROBOTS_UNKNOWN, error="scheme-refused")
        host = host_of(url)
        trail: list[str] = []
        robots_status = ROBOTS_NO_RULES
        if respect_robots:
            try:
                verdict = self.robots.check(url)
            except HostCooldown as exc:
                trail.append(f"robots:cooldown:{exc}")
                return self._result(url, trail=trail, robots=ROBOTS_UNKNOWN, error="cooldown")
            except BudgetExhausted as exc:
                trail.append(f"robots:budget:{exc}")
                return self._result(url, trail=trail, robots=ROBOTS_UNKNOWN, error="budget")
            robots_status = verdict["status"]
            trail.append(f"robots:{robots_status}")
            self.governor.set_floor(host, verdict.get("crawl_delay"))
            if robots_status == ROBOTS_DISALLOWED:
                return self._result(url, trail=trail, robots=robots_status, error="robots-disallowed")

        req = urllib.request.Request(url, headers=self._headers(url, conditional, extra_headers))
        if same_origin:
            req.pinned_origin = origin_of(url)
        attempts = 0
        failure: dict = {"status": 0, "error": "error", "headers": {}, "hops": []}
        while attempts <= self.retries:
            attempts += 1
            try:
                self.governor.wait(host)
            except HostCooldown as exc:
                trail.append(f"live:cooldown:{exc}")
                return self._result(url, trail=trail, robots=robots_status, error="cooldown", attempts=attempts)
            except BudgetExhausted as exc:
                trail.append(f"live:budget:{exc}")
                return self._result(url, trail=trail, robots=robots_status, error="budget", attempts=attempts)
            try:
                resp = self._opener(req, self.timeout)
            except RedirectRefused as exc:
                self.governor.note(host, 0, {})
                trail.append("live:redirect-refused")
                failure = {"status": 0, "error": "redirect-refused", "headers": {}, "hops": list(getattr(exc, "hops", []))}
                break
            except urllib.error.HTTPError as exc:
                headers = dict(exc.headers or {})
                self.governor.note(host, exc.code, headers)
                hops = list(getattr(exc, "hops", []))
                if exc.code == 304:
                    self.cache.note_response(url, 304, headers, None)
                    trail.append("live:304:unchanged")
                    return self._result(url, status=304, headers=headers, source_label="cache",
                                        cache_verdict="unchanged", trail=trail, robots=robots_status,
                                        attempts=attempts, hops=hops)
                try:
                    body, _ = read_body(exc, self.body_cap)
                except Exception:  # noqa: BLE001
                    body = b""
                failure = {"status": exc.code, "error": f"http-{exc.code}", "headers": headers, "hops": hops}
                if exc.code in REFUSAL_STATUSES:
                    failure["error"] = "refused"
                    trail.append(f"live:{exc.code}:refused")
                    break
                if exc.code == 403:
                    failure["error"] = "bot-wall" if looks_like_bot_wall(body) else "refused"
                    trail.append(f"live:403:{failure['error']}")
                    break
                trail.append(f"live:{exc.code}")
                if exc.code in RETRY_STATUSES and attempts <= self.retries:
                    self._backoff(attempts, headers, trail)
                    continue
                break
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError) as exc:
                self.governor.note(host, 0, {})
                timed_out = isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in str(exc).lower()
                failure = {"status": 0, "error": "timeout" if timed_out else "network-error", "headers": {}, "hops": []}
                trail.append(f"live:error:{type(exc).__name__}")
                if attempts <= self.retries:
                    self._backoff(attempts, {}, trail)
                    continue
                break
            # a response arrived
            headers = dict(resp.headers)
            body, truncated = read_body(resp, self.body_cap)
            status = int(getattr(resp, "status", 200) or 200)
            hops = list(getattr(resp, "hops", []))
            final_url = resp.geturl() if hasattr(resp, "geturl") else url
            self.governor.note(host, status, headers)
            verdict = self.cache.note_response(url, status, headers, body)
            trail.append(f"live:{status}:{verdict}" + (":truncated" if truncated else ""))
            label = "cache" if verdict == "unchanged" else "live"
            return self._result(url, final_url=final_url or url, status=status, body=body, headers=headers,
                                truncated=truncated, hops=hops, source_label=label, cache_verdict=verdict,
                                trail=trail, robots=robots_status, attempts=attempts)

        # live prong failed for good: the archive prong runs only when asked for, and is labelled
        if self.allow_wayback:
            return self._archive(url, trail, robots_status, failure["status"], attempts)
        return self._result(url, status=failure["status"], headers=failure["headers"], trail=trail,
                            robots=robots_status, error=failure["error"], attempts=attempts, hops=failure["hops"])

    def _backoff(self, attempt: int, headers: dict, trail: list) -> None:
        retry_after = _retry_after_seconds(headers.get("Retry-After") or headers.get("retry-after"), self._clock)
        delay = retry_after if retry_after is not None else float(2 ** attempt)
        delay = min(delay, RETRY_AFTER_CAP)
        try:
            self.governor.pause(delay)
            trail.append(f"backoff:{delay:.1f}")
        except BudgetExhausted:
            trail.append("backoff:budget")

    # -- the archive prong (labelled, never live)

    def _archive(self, url: str, trail: list, robots_status: str, live_status: int, attempts: int) -> FetchResult:
        api = WAYBACK_AVAILABLE + quote(url, safe="")
        try:
            self.governor.wait(host_of(api))
            resp = self._opener(urllib.request.Request(api, headers={"User-Agent": self.user_agent}), self.timeout)
            body, _ = read_body(resp, 1 << 20)
            self.governor.note(host_of(api), int(getattr(resp, "status", 200) or 200), dict(resp.headers))
            data = json.loads(body or b"{}")
        except Exception as exc:  # noqa: BLE001
            trail.append(f"archive:error:{type(exc).__name__}")
            return self._result(url, status=live_status, trail=trail, robots=robots_status,
                                error="archive-unavailable", attempts=attempts)
        closest = (data.get("archived_snapshots") or {}).get("closest") or {}
        snap_url, snap_ts = closest.get("url"), closest.get("timestamp")
        if not snap_url:
            trail.append("archive:no-snapshot")
            return self._result(url, status=live_status, trail=trail, robots=robots_status,
                                error="archive-no-snapshot", attempts=attempts)
        snap_host = host_of(snap_url)
        try:
            self.governor.wait(snap_host)
            req = urllib.request.Request(snap_url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"})
            resp = self._opener(req, self.timeout)
            body, truncated = read_body(resp, self.body_cap)
            status = int(getattr(resp, "status", 200) or 200)
            self.governor.note(snap_host, status, dict(resp.headers))
        except Exception as exc:  # noqa: BLE001
            self.governor.note(snap_host, 0, {})
            trail.append(f"archive:error:{type(exc).__name__}")
            return self._result(url, status=live_status, trail=trail, robots=robots_status,
                                error="archive-fetch-failed", attempts=attempts)
        trail.append(f"archive:{status}:{snap_ts}")
        return self._result(url, final_url=snap_url, status=status, body=body, truncated=truncated,
                            source_label="archive", trail=trail, robots=robots_status,
                            capture_url=snap_url, snapshot_ts=snap_ts, attempts=attempts,
                            hops=list(getattr(resp, "hops", [])))

    # -- state

    def save(self) -> None:
        self.governor.save()
        self.cache.save()


__all__ = [
    "BOT_WALL_FINGERPRINTS", "BREAKER_COOLDOWN", "BREAKER_FAILS", "BudgetExhausted",
    "DEFAULT_BODY_CAP", "DEFAULT_HOST_INTERVALS", "DEFAULT_MIN_INTERVAL", "DEFAULT_PACING_BUDGET",
    "DEFAULT_RETRIES", "DEFAULT_STATE_DIR", "DEFAULT_TIMEOUT", "FetchCache", "FetchResult",
    "Fetcher", "HopRecorder", "HostCooldown", "RateGovernor", "RedirectRefused", "RobotsCache",
    "default_opener", "host_of", "looks_like_bot_wall", "read_body", "utc_now_z",
]
