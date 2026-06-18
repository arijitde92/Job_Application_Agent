import os
import re
import json
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from crewai_tools import MCPServerAdapter
from app.core.logging import get_logger

load_dotenv()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic model for structured job details
# ---------------------------------------------------------------------------

class JobDetails(BaseModel):
    """Structured representation of a LinkedIn job posting."""

    url: str = Field(description="Source LinkedIn job posting URL")
    job_name: str = Field(default="N/A", description="Job title / role name")
    company_name: str = Field(default="N/A", description="Hiring company name")
    location: str = Field(default="N/A", description="Job location (city, country)")
    workplace_type: str = Field(
        default="N/A",
        description="Workplace type as shown on LinkedIn: 'On-site', 'Remote', 'Hybrid', or 'N/A' if not specified"
    )
    seniority_level: str = Field(default="N/A", description="Seniority level")
    employment_type: str = Field(default="N/A", description="Full-time / Part-time / Contract")
    job_function: str = Field(default="N/A", description="Job function / department")
    industry: str = Field(default="N/A", description="Company industry / sector")
    about_company: str = Field(default="N/A", description="Short description of the company")
    job_description: str = Field(
        default="N/A", description="Full job description including responsibilities"
    )
    requirements: str = Field(
        default="N/A", description="Must-have requirements and qualifications"
    )
    raw_markdown: Optional[str] = Field(
        default=None, exclude=True,
        description="Raw markdown scraped from the page (excluded from serialisation)"
    )

    def to_agent_string(self) -> str:
        """Return a clean JSON string suitable for passing to a CrewAI agent."""
        return self.model_dump_json(indent=2, exclude={"raw_markdown"})

    def to_dict(self) -> dict:
        """Return a plain dict (without raw_markdown)."""
        return self.model_dump(exclude={"raw_markdown"})


# ---------------------------------------------------------------------------
# Bright Data MCP server connection helper
# ---------------------------------------------------------------------------

def _get_mcp_server_params() -> dict:
    """Return the Bright Data MCP SSE server parameters."""
    api_token = os.getenv("BRIGHT_DATA_API_KEY")
    if not api_token:
        raise EnvironmentError(
            "BRIGHT_DATA_API_KEY is not set. Please add it to your .env file."
        )
    return {
        "url": f"https://mcp.brightdata.com/sse?token={api_token}",
        "transport": "sse",
    }


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------

def extract_linkedin_job_details(url: str, json_output: bool = False):
    """
    Scrape a LinkedIn job posting using the Bright Data MCP ``scrape_as_markdown``
    tool and return structured job details.

    Args:
        url (str): LinkedIn job posting URL.
        json_output (bool): If True return a JSON string, otherwise return a
                            ``JobDetails`` Pydantic model instance.

    Returns:
        JobDetails | str: Extracted job details.
    """
    logger.info(
        "linkedin_extractor: Scraping LinkedIn job URL via Bright Data MCP: %s", url
    )

    server_params = _get_mcp_server_params()
    markdown_content = ""

    try:
        with MCPServerAdapter(server_params) as mcp_tools:
            tool_names = [t.name for t in mcp_tools]
            logger.info(
                "linkedin_extractor: Connected to Bright Data MCP. Available tools: %s",
                tool_names,
            )

            if "scrape_as_markdown" not in tool_names:
                raise RuntimeError(
                    f"'scrape_as_markdown' not found in MCP tools: {tool_names}"
                )

            scrape_tool = next(t for t in mcp_tools if t.name == "scrape_as_markdown")
            logger.info("linkedin_extractor: Calling scrape_as_markdown for: %s", url)
            result = scrape_tool._run(url=url)
            markdown_content = str(result)
            logger.info(
                "linkedin_extractor: Scraping complete. Content length: %d chars",
                len(markdown_content),
            )

    except Exception as exc:
        logger.error(
            "linkedin_extractor: Failed to scrape %s — %s", url, exc, exc_info=True
        )
        error_model = JobDetails(url=url, job_name=f"ERROR: {exc}")
        return error_model.to_agent_string() if json_output else error_model

    job_details = _parse_markdown_to_job_details(markdown_content, url)

    if json_output:
        return job_details.to_agent_string()
    return job_details


# ---------------------------------------------------------------------------
# Markdown parser — tuned to LinkedIn's public job-posting markdown structure
# ---------------------------------------------------------------------------

def _parse_markdown_to_job_details(markdown: str, url: str) -> JobDetails:
    """
    Extract job fields from the markdown returned by Bright Data's
    ``scrape_as_markdown`` tool for a LinkedIn job posting.

    LinkedIn public page markdown layout (verified 2026-06):
      Line 1 (in a ``` fence):
        ``<Company> hiring <Title> in <City> | LinkedIn  [Skip to main content]...``
      ~Line 33:
        ``[<Company>](<company_url>) <City>, <Country>``  (company + location same line)
      ~Line 176+: sidebar block
        ``Seniority level``   (then blank line, then value)
        ``Employment type``
        ``Job function``
        ``Industries``
    """
    lines = [ln.rstrip() for ln in markdown.splitlines()]
    n = len(lines)

    def clean(text: str) -> str:
        """Strip markdown links, leaving only the link text."""
        return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text).strip()

    # ------------------------------------------------------------------
    # Helper: value on the next non-empty line after a keyword line
    # ------------------------------------------------------------------
    def value_after(keywords: list[str], search_from: int = 0, max_gap: int = 4) -> str:
        for i in range(search_from, n):
            if any(kw in lines[i].lower() for kw in keywords):
                for j in range(i + 1, min(i + max_gap + 1, n)):
                    candidate = clean(lines[j]).lstrip("#*->_ ").strip()
                    if candidate and not candidate.startswith("http"):
                        return candidate
        return "N/A"

    # ------------------------------------------------------------------
    # Helper: multi-line section between two keyword boundaries
    # ------------------------------------------------------------------
    def section_between(
        start_kws: list[str],
        end_kws: list[str],
        search_from: int = 0,
    ) -> str:
        start_idx = None
        for i in range(search_from, n):
            if any(kw in lines[i].lower() for kw in start_kws):
                start_idx = i + 1
                break
        if start_idx is None:
            return "N/A"

        end_idx = n
        for i in range(start_idx, n):
            if any(kw in lines[i].lower() for kw in end_kws):
                end_idx = i
                break

        chunk = [
            clean(l).strip()
            for l in lines[start_idx:end_idx]
            if l.strip() and not l.strip().startswith("http")
        ]
        return "\n".join(chunk).strip() or "N/A"

    # ------------------------------------------------------------------
    # 1. Job title + company from the page <title> in the opening code fence
    #    The title line is a single long line inside the ``` fence:
    #    "  <Company> hiring <Title> in <City> | LinkedIn  [Skip to...]"
    # ------------------------------------------------------------------
    job_name = "N/A"
    company_name_from_title = "N/A"

    # Match the code-fence line that contains "hiring" and "| LinkedIn"
    title_pattern = re.compile(
        r"^\s*(.+?)\s+hiring\s+(.+?)\s+in\s+.+?\s*\|\s*LinkedIn", re.IGNORECASE
    )
    for line in lines[:10]:
        m = title_pattern.search(line)
        if m:
            company_name_from_title = m.group(1).strip()
            job_name = m.group(2).strip()
            break

    # ------------------------------------------------------------------
    # 2. Company name — confirmed from LinkedIn company URL in topcard
    #    Pattern: [Company](https://XX.linkedin.com/company/slug ...) <Location>
    # ------------------------------------------------------------------
    company_name = company_name_from_title
    location = "N/A"

    topcard_pattern = re.compile(
        r"\[([^\]]+)\]\(https?://[a-z]+\.linkedin\.com/company/[^\)]+\)\s+(.+)"
    )
    for line in lines[:130]:
        m = topcard_pattern.search(line)
        if m:
            candidate_company = m.group(1).strip()
            candidate_location = m.group(2).strip()
            # Skip nav/logo links (they have no following text or are empty labels)
            if candidate_company and candidate_location:
                company_name = candidate_company
                location = candidate_location
                break

    # ------------------------------------------------------------------
    # 3. Workplace type — look ONLY within the topcard zone (first ~200 lines)
    #    LinkedIn renders On-site/Remote/Hybrid as JS badges that are NOT in
    #    static markdown. We can sometimes find them in structured text near
    #    the topcard. Searching the full document causes false positives from
    #    the "Similar jobs" sidebar which lists other remote/hybrid postings.
    # ------------------------------------------------------------------
    WORKPLACE_LABELS = {"on-site": "On-site", "remote": "Remote", "hybrid": "Hybrid"}
    workplace_type = "N/A"
    # Only inspect lines within the topcard zone, before job-description body
    topcard_zone = lines[:200]
    for line in topcard_zone:
        line_clean = line.strip().lower()
        # Skip lines that are links to other job postings (contain linkedin.com/jobs/view)
        if "linkedin.com/jobs/view" in line.lower():
            continue
        for label_lower, label_display in WORKPLACE_LABELS.items():
            # Use word boundary so 'remote' in 'remote-sensing' doesn't match
            if re.search(r"\b" + label_lower + r"\b", line_clean):
                workplace_type = label_display
                break
        if workplace_type != "N/A":
            break

    # ------------------------------------------------------------------
    # 4. Sidebar metadata — ``<Label>\n\n<Value>`` pattern
    # ------------------------------------------------------------------
    seniority      = value_after(["seniority level"])
    employment_type = value_after(["employment type"])
    job_function   = value_after(["job function"])
    industry       = value_after(["industries"])

    # ------------------------------------------------------------------
    # 5. About company — paragraph immediately following "About <Company>"
    # ------------------------------------------------------------------
    about_company = section_between(
        [f"about {company_name.lower()}", "about the company"],
        ["what you", "the role", "responsibilities", "your role", "show more"],
    )

    # ------------------------------------------------------------------
    # 6. Full job description (responsibilities + body)
    # ------------------------------------------------------------------
    desc_start_kws = [
        "what you'll do", "what you will do", "the role", "about this job",
        "job description", "position overview", "responsibilities",
        f"about {company_name.lower()}",
    ]
    desc_end_kws = [
        "must-have", "requirements", "qualifications",
        "good-to-have", "nice-to-have", "seniority level", "show more show less",
    ]
    job_description = section_between(desc_start_kws, desc_end_kws)

    # ------------------------------------------------------------------
    # 7. Requirements / qualifications
    # ------------------------------------------------------------------
    req_start_kws = ["must-have", "requirements", "qualifications", "about you"]
    req_end_kws   = [
        "good-to-have", "nice-to-have", "preferred", "why join",
        "culture", "show more", "seniority level",
    ]
    requirements = section_between(req_start_kws, req_end_kws)

    return JobDetails(
        url=url,
        job_name=job_name,
        company_name=company_name,
        location=location,
        workplace_type=workplace_type,
        seniority_level=seniority,
        employment_type=employment_type,
        job_function=job_function,
        industry=industry,
        about_company=about_company,
        job_description=job_description,
        requirements=requirements,
        raw_markdown=markdown,
    )


# ---------------------------------------------------------------------------
# Stand-alone test entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_url = "https://www.linkedin.com/jobs/view/4413867953/"
    logger.info("linkedin_extractor: Running standalone extraction for: %s", test_url)

    job: JobDetails = extract_linkedin_job_details(test_url)

    print("\n===== EXTRACTED JOB DETAILS (JobDetails Pydantic model) =====")
    print(job.to_agent_string())
    print(f"\n[raw_markdown] total length: {len(job.raw_markdown or '')} chars")
