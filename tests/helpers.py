"""Shared offline test doubles: a fake HTTP response, a scripted opener and a fake clock."""
from __future__ import annotations

import io
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


class FakeResp:
    """Looks enough like an http.client.HTTPResponse for fetchkit: status, headers, read(n),
    geturl() and context management."""

    def __init__(self, status=200, headers=None, body=b"data", url="https://example.invalid/"):
        self.status = status
        self.headers = headers or {}
        self._buf = io.BytesIO(body)
        self._url = url
        self.hops = []

    def read(self, amt=None):
        return self._buf.read(amt) if amt is not None else self._buf.read()

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, headers=None, body=b""):
    err = urllib.error.HTTPError(url, code, f"status {code}", headers or {}, io.BytesIO(body))
    return err


def make_opener(script, robots=None):
    """`script` is consumed one entry per non-robots call: a FakeResp, an Exception instance, or a
    callable(req, timeout). `robots` maps host -> FakeResp | Exception for /robots.txt; a host not
    listed gets a 404."""
    calls = []
    robots = robots or {}

    def opener(req, timeout):
        url = req.full_url
        calls.append(url)
        if url.endswith("/robots.txt"):
            host = url.split("//", 1)[1].split("/", 1)[0]
            item = robots.get(host)
            if item is None:
                raise http_error(url, 404)
            if isinstance(item, Exception):
                raise item
            if callable(item):
                return item(req, timeout)
            item._buf.seek(0)
            return item
        if not script:
            raise AssertionError(f"unexpected request {url}")
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, FakeResp):
            return item(req, timeout)
        return item

    opener.calls = calls
    return opener


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start
        self.slept = []

    def __call__(self):
        self.t += 0.001
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def read_fixture(*parts) -> bytes:
    return (FIXTURES.joinpath(*parts)).read_bytes()
