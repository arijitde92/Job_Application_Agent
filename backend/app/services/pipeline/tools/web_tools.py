"""
app.services.pipeline.tools.web_tools
-------------------------------------
Web-page scraping LangChain tool for the resume_strategist and
interview_preparer agents.

``scrape_website`` fetches one page and returns its visible text (scripts,
styles and page chrome removed, whitespace collapsed), capped so a long page
cannot flood the agent's context. Like ``web_search`` it never raises: every
failure comes back as an "ERROR: ..." observation so the agent can carry on
with the sources it already has.
"""

import re

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from app.core.logging import get_logger

logger = get_logger(__name__)

# A browser-like User-Agent: many career sites reject the requests default.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 20
MAX_PAGE_CHARS = 20_000

# Elements that never carry page content worth reading.
_NON_CONTENT_TAGS = ("script", "style", "noscript", "svg", "header", "footer", "nav")


def _page_text(html: bytes | str) -> str:
    """Extract the visible text of an HTML document, whitespace-collapsed."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(_NON_CONTENT_TAGS):
        element.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


@tool("scrape_website")
def scrape_website(website_url: str) -> str:
    """
    Fetch a web page and return its readable text content.

    Use this to read a specific page found via web_search — a company's
    careers or engineering-blog page, an interview-experience write-up, or a
    technology's documentation. Pass the full URL including https://.

    Args:
        website_url (str): The full URL of the page to read.

    Returns:
        str: The page's visible text (truncated to about 20,000 characters),
             or a string starting with "ERROR:" if the page could not be read.
    """
    url = (website_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "ERROR: 'website_url' must be a full URL starting with http:// or https://."

    try:
        response = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        logger.warning("web_tools: %s returned HTTP %s", url, status)
        return f"ERROR: The page returned HTTP status {status}. Continue without it."
    except requests.RequestException as e:
        logger.warning("web_tools: request to %s failed: %s", url, e)
        return f"ERROR: The page could not be fetched ({e}). Continue without it."

    text = _page_text(response.content)
    if not text:
        return "ERROR: The page has no readable text content. Continue without it."
    logger.info("web_tools: scraped %d chars from %s", len(text), url)
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + " ... [truncated]"
    return text
