"""
app.services.crew.search_tools
------------------------------
Web-search tool for the interview_preparer agent, backed by the Serper API
(https://serper.dev — a Google Search results API).

Unlike the GitHub and resume tools in this package, ``web_search`` carries no
per-run identity (there is nothing to scope it to), so it is a plain
module-level tool rather than a factory — the same reasoning that makes
``calculate_yoe`` a module-level tool in ``app.services.crew.resume_tools``.

The tool never raises. It runs in the LAST task of the crew, after the resume
has already been tailored, so a Serper outage, a missing key, or a rate limit
must degrade the interview prep to "no web results" rather than sink a run
whose expensive work is already done. Every failure comes back as an
"ERROR: ..." string, which CrewAI feeds to the agent as an observation; the
task prompt tells the agent to continue from the resume and job details alone
when that happens.

crewai_tools ships its own ``SerperDevTool``, which this replaces for the
interview_preparer. That tool returns the raw Serper JSON payload — knowledge
graph, people-also-ask, related searches, sitelinks and all — which burns
context on markup the agent does not need. This one returns a compact, ranked
text digest of just the answer box and the organic results.
"""

import os

import requests
from crewai.tools import tool

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"

# Serper accepts up to 100 results per call, but each one costs context in the
# agent's prompt and the tail of a Google result page is rarely useful for
# interview research.
MIN_RESULTS = 1
MAX_RESULTS = 10
DEFAULT_RESULTS = 5

# Serper is normally sub-second; this only guards against a hung connection.
REQUEST_TIMEOUT_SECONDS = 20


def _serper_api_key() -> str:
    """
    Resolve the Serper API key at call time (not import time).

    Reading it lazily keeps this module importable — and the rest of the crew
    runnable — when SERPER_API_KEY is not configured.
    """
    settings = get_settings()
    return settings.SERPER_API_KEY or os.environ.get("SERPER_API_KEY", "")


def _format_results(query: str, payload: dict, num_results: int) -> str:
    """Render Serper's JSON response as a compact digest for the agent."""
    lines = [f'Web search results for: "{query}"']

    # Serper's answer box is Google's featured snippet — when present it is
    # usually the single most direct answer to the query.
    answer_box = payload.get("answerBox") or {}
    answer = answer_box.get("answer") or answer_box.get("snippet")
    if answer:
        lines.append(f"\nFEATURED ANSWER: {answer}")
        if answer_box.get("link"):
            lines.append(f"Source: {answer_box['link']}")

    organic = payload.get("organic") or []
    if not organic:
        lines.append("\nNo organic results were returned for this query.")
        return "\n".join(lines)

    lines.append("")
    for position, result in enumerate(organic[:num_results], start=1):
        title = result.get("title", "(no title)")
        link = result.get("link", "")
        snippet = result.get("snippet", "")
        lines.append(f"{position}. {title}")
        if link:
            lines.append(f"   URL: {link}")
        if snippet:
            lines.append(f"   {snippet}")
        # Recency matters for "latest interview questions" style queries.
        if result.get("date"):
            lines.append(f"   Published: {result['date']}")
        lines.append("")

    return "\n".join(lines).rstrip()


@tool("web_search")
def web_search(query: str, num_results: int = DEFAULT_RESULTS) -> str:
    """
    Searches the web via Google and returns the top results for a query.

    Use this to research anything that is not in the resume or the job details:
    a company's interview process and culture, the interview rounds and formats
    a specific role is known to use, commonly asked questions for a technology
    or seniority level, or recent news about the company or its products.

    Write a natural search query, exactly as you would type it into Google
    (e.g. "Stripe backend engineer interview process rounds" or "common Kafka
    system design interview questions"). Search one topic per call and call the
    tool again for the next topic; do not stack several unrelated questions
    into one query.

    Args:
        query (str): The search query.
        num_results (int): How many organic results to return (1-10, default 5).

    Returns:
        str: A ranked digest of the results — title, URL and snippet for each,
             preceded by Google's featured answer when one exists. Returns a
             string starting with "ERROR:" if the search could not be run, in
             which case continue without web results.
    """
    query = (query or "").strip()
    if not query:
        return "ERROR: 'query' must be a non-empty search string."

    api_key = _serper_api_key()
    if not api_key:
        logger.warning("crew.search_tools: SERPER_API_KEY is not configured.")
        return (
            "ERROR: Web search is unavailable because SERPER_API_KEY is not "
            "configured. Continue without web results."
        )

    num_results = max(MIN_RESULTS, min(int(num_results), MAX_RESULTS))

    try:
        response = requests.post(
            SERPER_SEARCH_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num_results},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        logger.error(
            "crew.search_tools: Serper returned HTTP %s for query %r", status, query
        )
        return (
            f"ERROR: The web search failed with HTTP status {status}. "
            "Continue without web results for this query."
        )
    except requests.RequestException as e:
        logger.error(
            "crew.search_tools: Serper request failed for query %r: %s",
            query, e, exc_info=True,
        )
        return (
            f"ERROR: The web search request failed ({e}). Continue without web "
            "results for this query."
        )
    except ValueError as e:
        # Non-JSON body — a proxy error page or a truncated response.
        logger.error(
            "crew.search_tools: Serper returned a non-JSON response for query %r: %s",
            query, e,
        )
        return (
            "ERROR: The web search returned an unreadable response. Continue "
            "without web results for this query."
        )

    logger.info(
        "crew.search_tools: web_search returned %d organic results for %r",
        len(payload.get("organic") or []), query,
    )
    return _format_results(query, payload, num_results)
