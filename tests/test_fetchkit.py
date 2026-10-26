"""Offline tests for feeder/fetchkit.py: injected opener, fake clock, no network, no persisted state
unless the test creates a temporary directory for it."""
import gzip
import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import feeder
from feeder import fetchkit, jsonio
from tests.helpers import FakeClock, FakeResp, http_error, make_opener


def fetcher(script, robots=None, **kw):
    clock = FakeClock()
    kw.setdefault("state_dir", None)
    kw.setdefault("jitter", 0.0)
    f = fetchkit.Fetcher(opener=make_opener(script, robots), clock=clock, sleeper=clock.sleep, **kw)
    f.clock = clock
    return f


class DefaultsTests(unittest.TestCase):
    def test_defaults_match_the_brief(self):
        self.assertEqual(fetchkit.DEFAULT_MIN_INTERVAL, 2.0)
        self.assertEqual(fetchkit.DEFAULT_TIMEOUT, 20.0)
        self.assertEqual(fetchkit.DEFAULT_RETRIES, 2)
        self.assertEqual(fetchkit.DEFAULT_PACING_BUDGET, 600.0)
        self.assertEqual(fetchkit.DEFAULT_BODY_CAP, 5 * 1024 * 1024)
        self.assertEqual(fetchkit.DEFAULT_HOST_INTERVALS, {"web.archive.org": 6.0, "index.commoncrawl.org": 10.0})
        self.assertEqual(fetchkit.DEFAULT_STATE_DIR, feeder.ROOT / ".cache")
        f = fetchkit.Fetcher(state_dir=None)
        self.assertFalse(f.allow_wayback)
        self.assertEqual(f.governor.base_interval("web.archive.org"), 6.0)
        self.assertEqual(f.governor.base_interval("index.commoncrawl.org"), 10.0)
        self.assertEqual(f.governor.base_interval("www.nist.gov"), 2.0)
        self.assertEqual(f.user_agent, feeder.USER_AGENT)

    def test_constructor_parameters_are_honoured(self):
        f = fetchkit.Fetcher(state_dir=Path("/tmp/x"), min_interval=5, host_intervals={"a.example": 9},
                             pacing_budget=30, body_cap=100, timeout=3, retries=0)
        self.assertEqual(f.governor.state_path, Path("/tmp/x") / "host_limits.json")
        self.assertEqual(f.governor.base_interval("a.example"), 9.0)
        self.assertEqual(f.governor.base_interval("b.example"), 5.0)
        self.assertEqual(f.governor.pacing_budget, 30.0)
        self.assertEqual(f.body_cap, 100)
        self.assertEqual(f.timeout, 3.0)
        self.assertEqual(f.retries, 0)


class GovernorTests(unittest.TestCase):
    def gov(self, **kw):
        clock = FakeClock()
        kw.setdefault("jitter", 0.0)
        g = fetchkit.RateGovernor(None, clock=clock, sleeper=clock.sleep, **kw)
        g.clock = clock
        return g

    def test_two_second_pacing_per_host(self):
        g = self.gov()
        g.wait("h.example")
        g.wait("h.example")
        self.assertEqual(len(g.clock.slept), 1)
        self.assertAlmostEqual(g.clock.slept[0], 2.0, delta=0.01)
        g.wait("other.example")
        self.assertEqual(len(g.clock.slept), 1, "a different host is not paced against the first")

    def test_breaker_trips_after_four_and_counts_network_errors(self):
        g = self.gov()
        g.note("h.example", 503, {})
        g.note("h.example", 0, {})
        g.note("h.example", 429, {})
        g.wait("h.example")
        g.note("h.example", 0, {})
        with self.assertRaises(fetchkit.HostCooldown):
            g.wait("h.example")
        entry = g.hosts["h.example"]
        self.assertGreater(entry["breaker_until"], g.clock.t)
        self.assertAlmostEqual(entry["breaker_until"] - g.clock.t, fetchkit.BREAKER_COOLDOWN, delta=1.0)
        entry["breaker_until"] = 0.0
        g.wait("h.example")
        g.note("h.example", 200, {})
        self.assertEqual(g.hosts["h.example"]["fails"], 0)

    def test_retry_after_raises_interval_and_decays_after_three_clean(self):
        g = self.gov()
        g.note("h.example", 429, {"Retry-After": "30"})
        self.assertEqual(g.effective_interval("h.example"), 30.0)
        g.note("h.example", 200, {})
        g.note("h.example", 200, {})
        self.assertEqual(g.effective_interval("h.example"), 30.0, "two clean responses are not enough")
        g.note("h.example", 200, {})
        self.assertEqual(g.effective_interval("h.example"), 2.0, "three clean responses decay the interval")

    def test_retry_after_http_date_and_cap(self):
        g = self.gov()
        g.note("h.example", 503, {"Retry-After": "Thu, 01 Jan 2099 00:00:00 GMT"})
        self.assertEqual(g.effective_interval("h.example"), fetchkit.RETRY_AFTER_CAP)
        g2 = self.gov()
        g2.note("h.example", 503, {"Retry-After": "not a value"})
        self.assertEqual(g2.effective_interval("h.example"), 2.0)

    def test_host_override_and_crawl_delay_floor(self):
        g = self.gov()
        self.assertEqual(g.effective_interval("web.archive.org"), 6.0)
        g.set_floor("h.example", 7.5)
        self.assertEqual(g.effective_interval("h.example"), 7.5)
        g.note("h.example", 429, {"Retry-After": "20"})
        for _ in range(3):
            g.note("h.example", 200, {})
        self.assertEqual(g.effective_interval("h.example"), 7.5, "decay never drops below the floor")

    def test_pacing_budget(self):
        g = self.gov(pacing_budget=3.0)
        g.wait("h.example")
        g.wait("h.example")  # sleeps 2s
        with self.assertRaises(fetchkit.BudgetExhausted):
            g.wait("h.example")  # would sleep another 2s, past the 3s budget
        self.assertAlmostEqual(g.pacing_spent, 2.0, delta=0.01)

    def test_request_budget(self):
        g = self.gov(request_budget=2)
        g.wait("a.example")
        g.wait("b.example")
        with self.assertRaises(fetchkit.BudgetExhausted):
            g.wait("c.example")

    def test_state_round_trip_is_atomic_canonical_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "host_limits.json"
            clock = FakeClock()
            g = fetchkit.RateGovernor(path, clock=clock, sleeper=clock.sleep, jitter=0.0)
            g.note("persist.example", 429, {"Retry-After": "60"})
            g.save()
            self.assertTrue(jsonio.is_canonical(path))
            self.assertEqual([p.name for p in Path(td).iterdir()], ["host_limits.json"])
            g2 = fetchkit.RateGovernor(path, clock=clock, sleeper=clock.sleep, jitter=0.0)
            self.assertEqual(g2.effective_interval("persist.example"), 60.0)


class CacheTests(unittest.TestCase):
    def test_304_and_sha_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            c = fetchkit.FetchCache(Path(td) / "s.json", clock=FakeClock())
            self.assertEqual(c.note_response("http://x/y", 200, {"ETag": '"abc"', "Last-Modified": "lm"}, b"hello"), "changed")
            self.assertEqual(c.validators("http://x/y"), {"If-None-Match": '"abc"', "If-Modified-Since": "lm"})
            self.assertEqual(c.note_response("http://x/y", 304, {}, None), "unchanged")
            self.assertEqual(c.note_response("http://x/y", 200, {}, b"hello"), "unchanged")
            self.assertEqual(c.note_response("http://x/y", 200, {}, b"world"), "changed")
            c.save()
            c2 = fetchkit.FetchCache(Path(td) / "s.json")
            self.assertIsNotNone(c2.cached_sha("http://x/y"))
            self.assertRegex(c2.urls["http://x/y"]["checked_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class BodyTests(unittest.TestCase):
    def test_streaming_cap_sets_truncated(self):
        resp = FakeResp(200, {}, b"x" * 1000)
        body, truncated = fetchkit.read_body(resp, 100)
        self.assertEqual(len(body), 100)
        self.assertTrue(truncated)
        body, truncated = fetchkit.read_body(FakeResp(200, {}, b"short"), 100)
        self.assertEqual((body, truncated), (b"short", False))

    def test_gzip_and_mislabelled_encoding(self):
        payload = gzip.compress(b"hello gzip")
        body, truncated = fetchkit.read_body(FakeResp(200, {"Content-Encoding": "gzip"}, payload), 1000)
        self.assertEqual((body, truncated), (b"hello gzip", False))
        body, _ = fetchkit.read_body(FakeResp(200, {"Content-Encoding": "gzip"}, b"plain, not gzip"), 1000)
        self.assertEqual(body, b"plain, not gzip")
        big = gzip.compress(b"y" * 5000)
        body, truncated = fetchkit.read_body(FakeResp(200, {"Content-Encoding": "gzip"}, big), 1000)
        self.assertEqual(len(body), 1000)
        self.assertTrue(truncated, "the cap applies to the decompressed size too")


class RedirectTests(unittest.TestCase):
    def test_hop_recorder_records_and_refuses_downgrade(self):
        rec = fetchkit.HopRecorder()
        req = urllib.request.Request("https://a.example/x")
        new = rec.redirect_request(req, None, 301, "moved", {}, "https://b.example/y")
        self.assertIsNotNone(new)
        self.assertEqual(rec.hops, [{"from": "https://a.example/x", "to": "https://b.example/y", "status": 301}])
        with self.assertRaises(fetchkit.RedirectRefused):
            rec.redirect_request(urllib.request.Request("https://b.example/y"), None, 302, "found", {}, "http://b.example/y")
        self.assertEqual(len(rec.hops), 2, "the refused hop is still recorded")

    def test_fetcher_reports_refused_redirect(self):
        f = fetcher([fetchkit.RedirectRefused("refused https to http redirect")])
        res = f.get("https://a.example/x")
        self.assertEqual(res.error, "redirect-refused")
        self.assertIsNone(res.body)
        self.assertIn("live:redirect-refused", res.trail)


class RobotsTests(unittest.TestCase):
    def robots_resp(self, text, ctype="text/plain"):
        return FakeResp(200, {"Content-Type": ctype}, text.encode("utf-8"))

    def test_robots_fetched_with_ua_and_parsed_with_parse(self):
        robots = {"h.example": self.robots_resp("User-agent: *\nDisallow: /private\nCrawl-delay: 4\n")}
        f = fetcher([FakeResp(200, {}, b"ok", url="https://h.example/public")], robots)
        res = f.get("https://h.example/public")
        self.assertEqual(res.robots, "allowed")
        self.assertEqual(res.body, b"ok")
        self.assertEqual(f.governor.effective_interval("h.example"), 4.0, "Crawl-delay raises the floor")
        blocked = f.get("https://h.example/private/x")
        self.assertEqual(blocked.error, "robots-disallowed")
        self.assertEqual(blocked.robots, "disallowed")
        robots_calls = [c for c in f._opener.calls if c.endswith("/robots.txt")]
        self.assertEqual(robots_calls, ["https://h.example/robots.txt"], "robots.txt fetched once per host")

    def test_robots_401_403_is_unknown_and_allows(self):
        for code in (401, 403):
            robots = {"h.example": http_error("https://h.example/robots.txt", code)}
            f = fetcher([FakeResp(200, {}, b"ok")], robots)
            res = f.get("https://h.example/x")
            self.assertEqual(res.robots, "unknown", code)
            self.assertEqual(res.body, b"ok")

    def test_robots_5xx_is_disallowed_after_one_retry(self):
        robots = {"h.example": http_error("https://h.example/robots.txt", 503)}
        f = fetcher([], robots)
        res = f.get("https://h.example/x")
        self.assertEqual(res.error, "robots-disallowed")
        self.assertEqual(f._opener.calls, ["https://h.example/robots.txt", "https://h.example/robots.txt"])

    def test_robots_network_error_is_disallowed_after_one_retry(self):
        robots = {"h.example": urllib.error.URLError("dns failed")}
        f = fetcher([], robots)
        res = f.get("https://h.example/x")
        self.assertEqual(res.error, "robots-disallowed")
        self.assertEqual(len(f._opener.calls), 2)

    def test_html_served_as_robots_means_no_rules(self):
        robots = {"h.example": self.robots_resp("<!DOCTYPE html><html><body>Disallow: /</body></html>", "text/html")}
        f = fetcher([FakeResp(200, {}, b"ok")], robots)
        res = f.get("https://h.example/anything")
        self.assertEqual(res.robots, "no_rules")
        self.assertEqual(res.body, b"ok")

    def test_robots_404_means_no_rules(self):
        f = fetcher([FakeResp(200, {}, b"ok")])
        self.assertEqual(f.get("https://h.example/x").robots, "no_rules")

    def test_robots_never_uses_read(self):
        import inspect
        src = inspect.getsource(fetchkit.RobotsCache)
        self.assertNotIn(".read()", src)
        self.assertIn(".parse(", src)
        self.assertIn("RobotFileParser", src)


class LiveFetchTests(unittest.TestCase):
    def test_live_200_then_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            f = fetcher([FakeResp(200, {"ETag": '"e1"'}, b"body-1", url="https://live.example/a"),
                         FakeResp(200, {"ETag": '"e1"'}, b"body-1", url="https://live.example/a")],
                        state_dir=Path(td))
            res = f.get("https://live.example/a")
            self.assertEqual((res.status, res.body, res.source_label, res.cache_verdict), (200, b"body-1", "live", "changed"))
            self.assertEqual(res.final_url, "https://live.example/a")
            self.assertRegex(res.retrieved_at, r"Z$")
            self.assertEqual(res.robots, "no_rules")
            again = f.get("https://live.example/a")
            self.assertEqual((again.source_label, again.cache_verdict), ("cache", "unchanged"))
            f.save()
            self.assertTrue((Path(td) / "host_limits.json").exists())
            self.assertTrue((Path(td) / "fetch_state.json").exists())
            self.assertTrue(jsonio.is_canonical(Path(td) / "fetch_state.json"))

    def test_conditional_headers_sent_and_304_handled(self):
        seen = {}

        def capture(req, timeout):
            seen.update(dict(req.header_items()))
            raise http_error(req.full_url, 304)

        f = fetcher([FakeResp(200, {"ETag": '"e1"'}, b"b"), capture])
        f.get("https://live.example/a")
        res = f.get("https://live.example/a")
        self.assertEqual(seen.get("If-none-match"), '"e1"')
        self.assertEqual(seen.get("User-agent"), feeder.USER_AGENT)
        self.assertEqual((res.status, res.source_label, res.cache_verdict), (304, "cache", "unchanged"))

    def test_retries_twice_on_5xx_then_gives_up(self):
        f = fetcher([http_error("https://h.example/x", 502), http_error("https://h.example/x", 502),
                     http_error("https://h.example/x", 502)])
        res = f.get("https://h.example/x")
        self.assertEqual(res.attempts, 3)
        self.assertEqual(res.error, "http-502")
        self.assertEqual(res.status, 502)
        self.assertEqual([c for c in f._opener.calls if not c.endswith("robots.txt")], ["https://h.example/x"] * 3)
        backoffs = [t for t in res.trail if t.startswith("backoff:")]
        self.assertEqual(len(backoffs), 2)

    def test_recovers_on_retry(self):
        f = fetcher([http_error("https://h.example/x", 503, {"Retry-After": "1"}), FakeResp(200, {}, b"fine")])
        res = f.get("https://h.example/x")
        self.assertEqual(res.body, b"fine")
        self.assertEqual(res.attempts, 2)
        self.assertIn("backoff:1.0", res.trail)

    def test_network_errors_retry_and_count_toward_breaker(self):
        f = fetcher([urllib.error.URLError("boom"), socket.timeout("timed out"), urllib.error.URLError("boom")])
        res = f.get("https://h.example/x")
        self.assertEqual(res.error, "network-error")
        self.assertEqual(f.governor.hosts["h.example"]["fails"], 3)
        f2 = fetcher([socket.timeout("timed out")], retries=0)
        self.assertEqual(f2.get("https://h.example/y").error, "timeout")

    def test_401_451_and_plain_403_stop_retries(self):
        for code in (401, 451, 403):
            f = fetcher([http_error("https://h.example/x", code, body=b"<html>nope</html>")])
            res = f.get("https://h.example/x")
            self.assertEqual(res.error, "refused", code)
            self.assertEqual(res.attempts, 1, code)
            self.assertEqual(res.status, code)

    def test_403_with_bot_wall_fingerprint(self):
        f = fetcher([http_error("https://h.example/x", 403, body=b"<html><title>Just a moment...</title></html>")])
        res = f.get("https://h.example/x")
        self.assertEqual(res.error, "bot-wall")
        self.assertEqual(res.attempts, 1)
        self.assertTrue(fetchkit.looks_like_bot_wall(b"Attention Required! | Cloudflare"))
        self.assertFalse(fetchkit.looks_like_bot_wall(b"<html>forbidden</html>"))

    def test_body_cap_flags_truncated(self):
        f = fetcher([FakeResp(200, {}, b"z" * 500)], body_cap=100)
        res = f.get("https://h.example/big")
        self.assertTrue(res.truncated)
        self.assertEqual(len(res.body), 100)
        self.assertTrue(any(t.endswith(":truncated") for t in res.trail))

    def test_cooldown_short_circuits(self):
        f = fetcher([])
        f.governor._entry("h.example")["breaker_until"] = f.clock.t + 1000
        res = f.get("https://h.example/x")
        self.assertEqual(res.error, "cooldown")
        self.assertEqual([c for c in f._opener.calls if not c.endswith("robots.txt")], [])

    def test_hops_from_response_are_carried(self):
        resp = FakeResp(200, {}, b"ok", url="https://b.example/final")
        resp.hops = [{"from": "https://a.example/x", "to": "https://b.example/final", "status": 301}]
        f = fetcher([resp])
        res = f.get("https://a.example/x")
        self.assertEqual(res.final_url, "https://b.example/final")
        self.assertEqual(res.hops, resp.hops)
        rec = res.as_record()
        self.assertEqual(rec["final_url"], "https://b.example/final")
        self.assertEqual(rec["source_label"], "live")


class ArchiveTests(unittest.TestCase):
    def test_archive_is_off_by_default_and_never_consulted(self):
        f = fetcher([http_error("https://dead.example/x", 500)], retries=0)
        res = f.get("https://dead.example/x")
        self.assertEqual(res.source_label, "live")
        self.assertIsNone(res.capture_url)
        self.assertFalse(any("archive.org" in c for c in f._opener.calls))

    def test_archive_is_labelled_and_capture_url_recorded_only_after_fetch(self):
        wb = json.dumps({"archived_snapshots": {"closest": {
            "url": "https://web.archive.org/web/20260101000000/https://dead.example/x",
            "timestamp": "20260101000000"}}}).encode()
        f = fetcher([http_error("https://dead.example/x", 500), FakeResp(200, {}, wb),
                     FakeResp(200, {}, b"archived-body", url="https://web.archive.org/web/20260101000000/https://dead.example/x")],
                    retries=0, allow_wayback=True)
        res = f.get("https://dead.example/x")
        self.assertEqual(res.source_label, "archive")
        self.assertEqual(res.body, b"archived-body")
        self.assertEqual(res.capture_url, "https://web.archive.org/web/20260101000000/https://dead.example/x")
        self.assertEqual(res.snapshot_ts, "20260101000000")
        self.assertEqual(res.final_url, res.capture_url)
        self.assertTrue(any(c.startswith("https://archive.org/wayback/available?url=https%3A%2F%2Fdead.example") for c in f._opener.calls))

    def test_archive_snapshot_fetch_failure_records_no_capture_url(self):
        wb = json.dumps({"archived_snapshots": {"closest": {
            "url": "https://web.archive.org/web/20260101000000/https://dead.example/x",
            "timestamp": "20260101000000"}}}).encode()
        f = fetcher([http_error("https://dead.example/x", 500), FakeResp(200, {}, wb),
                     urllib.error.URLError("archive down")], retries=0, allow_wayback=True)
        res = f.get("https://dead.example/x")
        self.assertEqual(res.error, "archive-fetch-failed")
        self.assertIsNone(res.capture_url)
        self.assertEqual(res.source_label, "live")

    def test_no_archive_on_success(self):
        f = fetcher([FakeResp(200, {}, b"fine")], allow_wayback=True)
        f.get("https://ok.example/z")
        self.assertFalse(any("archive.org" in c for c in f._opener.calls))


class SchemeTests(unittest.TestCase):
    def test_only_http_and_https_are_fetched(self):
        from feeder.fetchkit import Fetcher
        fetcher = Fetcher(state_dir=None, opener=lambda req, timeout: (_ for _ in ()).throw(AssertionError("opened")))
        for url in ("file:///etc/hostname", "ftp://example.gov/x", "data:text/plain,x", "example.gov/x"):
            with self.subTest(url=url):
                result = fetcher.get(url)
                self.assertEqual((result.error, result.body, result.trail), ("scheme-refused", None, ["scheme-refused"]))


if __name__ == "__main__":
    unittest.main()
