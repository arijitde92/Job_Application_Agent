"""
Tests for the LinkedIn extractor's Bright Data MCP integration
(``langchain-mcp-adapters``), with the MCP client and tools faked.
"""

from contextlib import asynccontextmanager

import langchain_mcp_adapters.client as mcp_client
import langchain_mcp_adapters.tools as mcp_tools
import pytest

from app.services.extractors import linkedin_extractor
from app.services.extractors.linkedin_extractor import (
    _tool_result_text,
    extract_linkedin_job_details,
)

LINKEDIN_MARKDOWN = """```
  Acme Robotics hiring Backend Engineer in Berlin, Germany | LinkedIn  [Skip to main content]
```
[Acme Robotics](https://de.linkedin.com/company/acme-robotics) Berlin, Germany

Seniority level

Mid-Senior level

Employment type

Full-time
"""


class FakeTool:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def fake_mcp(monkeypatch):
    """Install a fake MultiServerMCPClient + load_mcp_tools; return the tool list."""
    monkeypatch.setenv("BRIGHT_DATA_API_KEY", "bd-token")
    state = {"tools": [], "connections": None, "load_kwargs": None}

    class FakeClient:
        def __init__(self, connections):
            state["connections"] = connections

        @asynccontextmanager
        async def session(self, name):
            yield f"session:{name}"

    async def fake_load(session, **kwargs):
        state["load_kwargs"] = kwargs
        return state["tools"]

    monkeypatch.setattr(mcp_client, "MultiServerMCPClient", FakeClient)
    monkeypatch.setattr(mcp_tools, "load_mcp_tools", fake_load)
    return state


def test_scrapes_via_mcp_and_parses_job_details(fake_mcp):
    scrape = FakeTool("scrape_as_markdown", result=[{"type": "text", "text": LINKEDIN_MARKDOWN}])
    fake_mcp["tools"] = [FakeTool("search_engine"), scrape]

    job = extract_linkedin_job_details("https://www.linkedin.com/jobs/view/1/")

    assert scrape.calls == [{"url": "https://www.linkedin.com/jobs/view/1/"}]
    assert fake_mcp["connections"] == {
        "brightdata": {"url": "https://mcp.brightdata.com/sse?token=bd-token", "transport": "sse"}
    }
    # Scrape failures must raise, not come back as page text.
    assert fake_mcp["load_kwargs"] == {"handle_tool_errors": False}
    assert job.job_name == "Backend Engineer"
    assert job.company_name == "Acme Robotics"
    assert job.seniority_level == "Mid-Senior level"
    assert job.employment_type == "Full-time"


def test_missing_scrape_tool_returns_error_model(fake_mcp):
    fake_mcp["tools"] = [FakeTool("search_engine")]
    job = extract_linkedin_job_details("https://www.linkedin.com/jobs/view/1/")
    assert job.job_name.startswith("ERROR:")
    assert "scrape_as_markdown" in job.job_name


def test_tool_failure_returns_error_model_as_json(fake_mcp):
    fake_mcp["tools"] = [FakeTool("scrape_as_markdown", error=RuntimeError("quota exceeded"))]
    result = extract_linkedin_job_details("https://www.linkedin.com/jobs/view/1/", json_output=True)
    assert '"job_name": "ERROR: quota exceeded"' in result


def test_missing_api_key_returns_error_model(monkeypatch):
    monkeypatch.delenv("BRIGHT_DATA_API_KEY", raising=False)
    job = extract_linkedin_job_details("https://www.linkedin.com/jobs/view/1/")
    assert "BRIGHT_DATA_API_KEY" in job.job_name


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("plain", "plain"),
        ([{"type": "text", "text": "a"}, {"type": "image", "base64": "..."}, "b"], "a\nb"),
        (42, "42"),
    ],
)
def test_tool_result_text(result, expected):
    assert _tool_result_text(result) == expected


async def test_works_when_called_inside_a_running_event_loop(fake_mcp):
    fake_mcp["tools"] = [FakeTool("scrape_as_markdown", result=LINKEDIN_MARKDOWN)]
    job = linkedin_extractor.extract_linkedin_job_details("https://www.linkedin.com/jobs/view/1/")
    assert job.company_name == "Acme Robotics"
