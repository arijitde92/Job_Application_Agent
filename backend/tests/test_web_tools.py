"""Tests for the ``scrape_website`` LangChain tool (requests monkeypatched)."""

import requests

import app.services.pipeline.tools.web_tools as web_tools
from app.services.pipeline.tools import scrape_website

PAGE = b"""
<html><head><title>Careers</title><style>.x{}</style><script>var a=1;</script></head>
<body><nav>Home | Jobs</nav>
<h1>Interview process</h1>
<p>Three   rounds:
   screen, system design, onsite.</p>
<footer>(c) Acme</footer></body></html>
"""


class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_extracts_visible_text_only(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return FakeResponse(200, PAGE)

    monkeypatch.setattr(web_tools.requests, "get", fake_get)
    text = scrape_website.invoke({"website_url": "https://acme.example/careers"})

    assert "Interview process" in text
    assert "Three rounds: screen, system design, onsite." in text
    for noise in ("var a=1", ".x{}", "Home | Jobs", "(c) Acme"):
        assert noise not in text
    assert "Mozilla" in seen["headers"]["User-Agent"]
    assert seen["timeout"] == web_tools.REQUEST_TIMEOUT_SECONDS


def test_long_pages_are_truncated(monkeypatch):
    big = b"<p>" + b"word " * 10_000 + b"</p>"
    monkeypatch.setattr(web_tools.requests, "get", lambda *a, **k: FakeResponse(200, big))
    text = scrape_website.invoke({"website_url": "https://acme.example"})
    assert text.endswith("[truncated]")
    assert len(text) <= web_tools.MAX_PAGE_CHARS + 20


def test_http_error_is_an_error_string(monkeypatch):
    monkeypatch.setattr(web_tools.requests, "get", lambda *a, **k: FakeResponse(404))
    assert scrape_website.invoke({"website_url": "https://acme.example"}) == (
        "ERROR: The page returned HTTP status 404. Continue without it."
    )


def test_connection_error_is_an_error_string(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(web_tools.requests, "get", boom)
    assert scrape_website.invoke({"website_url": "https://acme.example"}).startswith("ERROR:")


def test_rejects_non_http_urls():
    assert scrape_website.invoke({"website_url": "file:///etc/passwd"}).startswith("ERROR:")
