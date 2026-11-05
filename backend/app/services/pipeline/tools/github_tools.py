"""
app.services.pipeline.tools.github_tools
----------------------------------------
Per-run LangChain tool factories for the github_project_summarizer agent.

Both GitHub tools are built fresh for every pipeline run (like the save tool
in ``app.services.pipeline.tools.resume_tools``) so that the applicant's
GitHub identity is *closured into the tool* rather than supplied by the LLM.

That is not a style preference. ``repo_content_searcher`` previously searched
the entire vector store with no filter, so one applicant's run could retrieve
another applicant's repository content and summarise it as the applicant's own
work. The identity has to come from the caller — an agent cannot be trusted to
pass the right ``repo_username``, and there is nothing in the prompt that would
stop it passing someone else's.
"""

import os
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import BaseTool, tool
from weaviate.exceptions import WeaviateBaseError

from app.core.logging import get_logger
from app.services.extractors.github_extractor import (
    process_github_repo_to_vector_store,
    query_github_vector_store,
)

logger = get_logger(__name__)

GITHUB_REPO_SEARCH_LIMIT = 10   # Number of repositories to scan and index

# Job descriptions run to several thousand characters. The full text is useful
# context for the query embedding, but rerank cost is
# query_tokens x n_candidates, so the context is capped before it is prepended.
MAX_JOB_DESCRIPTION_CHARS = 1500


@dataclass
class GithubIndexContext:
    """
    Per-run GitHub identity for the indexing and search tools.

    Attributes:
        github_url: The applicant's GitHub profile URL.
        github_username: The account name every vector search is scoped to.
        repo_limit: Maximum repositories to index.
    """

    github_url: str
    github_username: str
    repo_limit: int = GITHUB_REPO_SEARCH_LIMIT


def github_username_from_url(github_url: str) -> str:
    """Extract the account name from a GitHub profile URL."""
    return github_url.rstrip("/").split("/")[-1].split("?")[0]


def _get_repository_links(user_url: str, github_repo_urls: List[str]) -> List[str]:
    """
    Recursively fetch all repository links from a GitHub user page.

    Args:
        user_url: The URL of the GitHub user page.
        github_repo_urls: Accumulator for repo URLs.

    Returns:
        List[str]: A list of repository URLs.
    """
    if user_url[-1] == '/':
        user_url = user_url[:-1]

    user_url = user_url + "?tab=repositories"
    # Extract the username from the URL
    user_name = user_url.split('/')[-1].split('?')[0]

    # Fetch the url of each repository
    logger.info("github_tools: Searching URL: %s", user_url)
    response = requests.get(user_url, headers={'User-Agent': "Chrome/51.0.2704.106"})
    if response.status_code != 200:
        logger.error("github_tools: Error Occurred: Response Code: %s", response.status_code)
        return github_repo_urls
    html_content = response.content
    soup = BeautifulSoup(html_content, 'html.parser')
    repo_headings = soup.select('h3.wb-break-all')
    for repo_heading in repo_headings:
        repo_name = repo_heading.a.attrs["href"].split('/')[-1]
        link = 'https://github.com/' + user_name + "/" + repo_name
        logger.info("github_tools: Found repo: %s", link)
        github_repo_urls.append(link)
    pages = soup.find_all(attrs={"class": "next_page"})
    if len(pages) > 0:
        # find the next page link if <a href> exists
        if pages[0].name == 'a':
            next_page_link = 'https://github.com' + pages[0].attrs['href']
            github_repo_urls = _get_repository_links(next_page_link, github_repo_urls)
    return github_repo_urls


def make_extract_github_repos_tool(ctx: GithubIndexContext) -> BaseTool:
    """
    Build the repo-indexing tool bound to ``ctx``'s GitHub profile.

    Args:
        ctx: The applicant's GitHub identity.

    Returns:
        The ``github_repos_extractor`` LangChain tool.
    """

    @tool("github_repos_extractor")
    def extract_github_repos_tool() -> List[str] | str:
        """
        Indexes the applicant's public GitHub repositories into the vector store.

        Scrapes the applicant's GitHub profile for their public repositories and
        indexes the contents of each one so that repo_content_searcher can find
        them. Takes no arguments — the applicant's GitHub profile is already
        known. Call this once before searching.

        Returns:
            List[str]: The repository URLs that were indexed, or an "ERROR: ..."
                string when no public repositories could be found.
        """
        github_repo_urls = _get_repository_links(ctx.github_url, [])
        if not github_repo_urls:
            logger.warning("github_tools: No repositories found or an error occurred.")
            return (
                "ERROR: No public repositories were found on the applicant's GitHub "
                "profile. Report an empty \"chosen_github_repos\" list."
            )
        logger.info(
            "github_tools: Found %d repositories for user %s",
            len(github_repo_urls), ctx.github_url,
        )

        selected = github_repo_urls[:ctx.repo_limit]
        for repo_url in selected:
            try:
                process_github_repo_to_vector_store(
                    repo_url,
                    file_filter=lambda file_path: file_path.endswith(
                        ('.py', '.ipynb', '.md', '.txt')
                    ),
                    access_token=os.environ.get('GITHUB_PERSONAL_ACCESS_TOKEN'),
                )
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status == 401:
                    # Same bad token for every repo — stop instead of
                    # repeating the failure (and traceback) for each one.
                    logger.error(
                        "github_tools: GitHub rejected GITHUB_PERSONAL_ACCESS_TOKEN "
                        "(401 Unauthorized). Skipping repo indexing; renew the token "
                        "in backend/.env."
                    )
                    break
                logger.error("github_tools: Failed to index %s: %s", repo_url, e)
            except WeaviateBaseError as e:
                logger.error(
                    "github_tools: Weaviate unavailable (%s). Skipping repo indexing; "
                    "check WEAVIATE_URL / WEAVIATE_API_KEY and the cluster status.",
                    e,
                )
                break
            except Exception as e:
                # One unreadable repo (empty, non-default branch, rate limited)
                # must not sink the whole indexing pass.
                logger.error(
                    "github_tools: Failed to index %s: %s", repo_url, e, exc_info=True
                )
        return selected

    return extract_github_repos_tool


def make_repo_content_searcher_tool(ctx: GithubIndexContext) -> BaseTool:
    """
    Build the repo-search tool scoped to ``ctx``'s GitHub account.

    Args:
        ctx: The applicant's GitHub identity.

    Returns:
        The ``repo_content_searcher`` LangChain tool.
    """

    @tool("repo_content_searcher")
    def repo_content_searcher(
        query: str, job_description: Optional[str] = None, top_k: int = 5
    ) -> List[dict]:
        """
        Searches the applicant's indexed GitHub repositories for relevant content.

        Results are always restricted to the applicant's own repositories.

        Args:
            query (str): The search query.
            job_description (str): The job description (optional, used as context).
            top_k (int): Number of top results to return.

        Returns:
            List[dict]: List of relevant content chunks with metadata.
        """
        context = ""
        if job_description:
            context += f"Job Description: {job_description[:MAX_JOB_DESCRIPTION_CHARS]}\n"
        full_query = f"{context}\nQuery: {query}"
        results = query_github_vector_store(
            full_query, top_k=top_k, repo_username=ctx.github_username
        )
        return [
            {
                "content": content,
                "metadata": metadata
            } for content, metadata in results
        ]

    return repo_content_searcher
