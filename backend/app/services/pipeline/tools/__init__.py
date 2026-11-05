"""
app.services.pipeline.tools
---------------------------
LangChain tools for the pipeline's agents.

Tools that carry per-run identity (the applicant's resume, user/resume ids,
GitHub account, job/output path) are built by ``make_*`` factories so the
identity is closured into the tool rather than supplied by the LLM. Stateless
tools (``calculate_yoe``, ``web_search``, ``scrape_website``) are module-level.
"""

from app.services.pipeline.tools.docx_tools import (
    ResumeDocxContext,
    build_docx_filename,
    make_generate_resume_docx_tool,
)
from app.services.pipeline.tools.github_tools import (
    GithubIndexContext,
    github_username_from_url,
    make_extract_github_repos_tool,
    make_repo_content_searcher_tool,
)
from app.services.pipeline.tools.resume_tools import (
    ResumeAnalysisContext,
    calculate_yoe,
    make_read_resume_tool,
    make_save_parsed_resume_tool,
)
from app.services.pipeline.tools.search_tools import web_search
from app.services.pipeline.tools.web_tools import scrape_website

__all__ = [
    "GithubIndexContext",
    "ResumeAnalysisContext",
    "ResumeDocxContext",
    "build_docx_filename",
    "calculate_yoe",
    "github_username_from_url",
    "make_extract_github_repos_tool",
    "make_generate_resume_docx_tool",
    "make_read_resume_tool",
    "make_repo_content_searcher_tool",
    "make_save_parsed_resume_tool",
    "scrape_website",
    "web_search",
]
