from crewai import Agent, LLM
from crewai_tools import (
  FileReadTool,
  ScrapeWebsiteTool,
  MDXSearchTool,
  SerperDevTool
)
from crewai.tools import tool
import os
from dotenv import load_dotenv
from app.services.extractors.linkedin_extractor import extract_linkedin_job_details, JobDetails
from app.services.crew.search_tools import web_search
from app.services.crew.zai_llm import ZaiLLM
from app.core.logging import get_logger
load_dotenv()
logger = get_logger(__name__)

# LLM configuration — Gemini
gemini_llm = LLM(
    model="gemini/gemini-3.5-flash-lite",
    api_key=os.environ.get("GEMINI_API_KEY"),
    temperature=0.5,
    max_tokens=8000
)

# GLM-5.2 via Z.ai (github_project_summarizer). CrewAI has no native GLM
# provider, so ZaiLLM drives CrewAI's native OpenAI client against Z.ai's
# OpenAI-compatible endpoint — see app.services.crew.zai_llm for why the
# plain LLM(...) factory cannot express this.
#
# max_tokens is deliberately large: GLM-5.2 reasons by default and those
# reasoning tokens are charged against max_tokens, so a tight cap gets the
# response truncated (finish_reason="length") before any content is emitted.
glm_llm = ZaiLLM(
    model="glm-5.2",
    temperature=0.3,
    max_tokens=16000,
)

# Structured-extraction LLM (resume_analyzer): Anthropic Claude Sonnet 5. The
# parsed-resume JSON must be reproduced verbatim and can exceed 8k tokens.
#
# Three things about this config are load-bearing:
#   - The "anthropic/" prefix is REQUIRED. It routes CrewAI to its native
#     Anthropic client (which strips the prefix before calling the API). A bare
#     "claude-sonnet-5" falls through to CrewAI's OpenAI-compatible provider
#     instead, which cannot talk to Anthropic.
#   - No temperature / top_p. Claude Sonnet 5 rejects non-default sampling
#     parameters with a 400; CrewAI only sends them when they are set, so they
#     are omitted here. Extraction determinism comes from the task prompt and
#     the save-tool schema validation instead.
#   - No thinking config. Sonnet 5 runs adaptive thinking by default when the
#     field is absent, which is what we want. Do NOT add CrewAI's
#     thinking={"type": "enabled", "budget_tokens": N} — the fixed-budget form
#     was removed on Sonnet 5 and returns a 400. Note that max_tokens now caps
#     thinking + response together.
claude_llm_extraction = LLM(
    model="anthropic/claude-sonnet-5",
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    max_tokens=16000
)

search_tool = SerperDevTool()
scrape_tool = ScrapeWebsiteTool()
read_resume = FileReadTool()
semantic_search_resume = MDXSearchTool()

@tool("linkedin_job_extractor")
def extract_linkedin_job_details_tool(url: str) -> str:
    """
    Extracts job details from a LinkedIn job posting page using Bright Data MCP.
    Scrapes the page and returns a JSON-formatted string containing structured
    job information including company name, job title, location, seniority level,
    employment type, job function, industry, job description, and requirements.

    Args:
        url (str): The URL of the LinkedIn job posting.

    Returns:
        str: A JSON-formatted string containing the extracted job details.
             Returns an error message JSON if scraping fails.
    """
    logger.info("crew.agents: extract_linkedin_job_details_tool called for URL: %s", url)
    job: JobDetails = extract_linkedin_job_details(url)
    logger.info(
        "crew.agents: Extracted job '%s' at '%s'",
        job.job_name, job.company_name
    )
    return job.to_agent_string()


# NOTE: The Researcher agent has been removed. Job extraction is done
# directly in Job_Applier.py via extract_linkedin_job_details() and the
# structured JobDetails are passed into every task via crew input variables.

# Agent 1: GitHub Project Summarizer
# No agent-level tools: the per-run indexing and search tools (closured over the
# applicant's GitHub identity, see app.services.crew.github_tools) are attached
# at the Task level by build_tasks(), which keeps this module-level singleton
# thread-safe across concurrent crew runs and stops one applicant's search from
# reaching another applicant's repositories.
github_project_summarizer = Agent(
    role="GitHub Project Summarizer",
    goal="Summarize the user's most relevant GitHub projects for a job application, highlighting tech stacks, languages, frameworks, tools, and cloud technologies used.",
    llm=glm_llm,
    verbose=True,
    max_iter=8,
    max_rpm=10,
    respect_context_window=True,
    backstory=(
        "You are an expert in analyzing GitHub repositories and summarizing project experience for job applications. "
        "You first ensure the user's repositories are indexed in the vector store using the github_repos_extractor tool. "
        "Then, you use the repo_content_searcher tool to find the most relevant project content based on the job description and resume. "
        "Finally, you create a concise summary of the user's relevant GitHub projects, highlighting the tech stacks, programming languages, frameworks, tools, and any cloud computing technologies used."
    )
)


# Agent 2: Resume Analyzer
# No agent-level tools: the per-run save_parsed_resume tool (closured over
# user/resume IDs) is attached at the Task level by build_tasks(), which keeps
# this module-level singleton thread-safe across concurrent crew runs.
resume_analyzer = Agent(
    role="Resume Analyzer",
    goal=(
        "Carefully scan a resume and extract every piece of relevant applicant "
        "information into a strictly valid JSON document matching the required "
        "schema, using the calculate_yoe tool to derive the applicant's total "
        "years of experience from their position dates."
    ),
    llm=claude_llm_extraction,
    verbose=True,
    max_iter=8,
    max_rpm=10,
    respect_context_window=True,
    backstory=(
        "You are a meticulous structured-data extraction specialist for resumes. "
        "You read every line of a resume and map each fact to the correct field of "
        "a fixed JSON schema. You never invent, guess, or embellish information: "
        "every value you output must be traceable to explicit text in the resume, "
        "and anything the resume does not state is left as an empty string, null, an "
        "empty list, or false, exactly as the schema prescribes. When the resume "
        "groups skills under category headings you preserve those headings verbatim; "
        "when it does not, you place the skills under a single \"Default\" category. "
        "You never do date arithmetic in your head: once you have every position's "
        "start_date and end_date, you call the calculate_yoe tool once with all of "
        "those (start_date, end_date) pairs — passing null as the end_date of the "
        "applicant's current position, for which the tool substitutes today's date — "
        "and you copy the decimal number it returns straight into the "
        "\"years_of_experience\" field. If the tool reports a problem with one of "
        "the pairs, you correct that pair and call it again. "
        "You always produce strictly valid JSON — no comments, no trailing commas, "
        "no markdown fences — and you always persist your work with the "
        "save_parsed_resume tool before finishing."
    )
)

# Agent 3: Profiler
profiler = Agent(
    role="Personal Profiler for Engineers",
    goal="Do incredible analytical research on job applicants to help them stand out in the job market",
    tools=[read_resume, semantic_search_resume],
    llm=gemini_llm,
    verbose=True,
    max_iter=8,
    max_rpm=10,
    respect_context_window=True,
    backstory=(
        "Equipped with analytical prowess, you dissect and synthesize information "
        "from diverse sources to craft comprehensive personal and professional profiles,"
        "laying the groundwork for personalized resume enhancements."
    )
)

# Agent 4: Resume Strategist
resume_strategist = Agent(
    role="Resume Strategist for Engineers",
    goal="Find all the best ways to make a resume stand out in the job market.",
    tools=[scrape_tool, search_tool, read_resume, semantic_search_resume],
    llm=gemini_llm,
    verbose=True,
    max_iter=8,
    max_rpm=10,
    respect_context_window=True,
    backstory=(
        "With a strategic mind and an eye for detail, you "
        "excel at refining resumes to highlight the most "
        "relevant skills and experiences, ensuring they "
        "resonate perfectly with the job's requirements."
    )
)

# Agent 5: Interview Preparer
# Uses the project's own `web_search` (app.services.crew.search_tools) instead of
# crewai_tools' SerperDevTool: same Serper API underneath, but it returns a
# compact ranked digest rather than the raw JSON payload, and it degrades to an
# "ERROR: ..." observation instead of raising — this is the crew's final task,
# so a search outage must not sink a run that has already tailored the resume.
interview_preparer = Agent(
    role="Engineering Interview Preparer",
    goal="Create interview questions and talking points "
         "based on the resume, job requirements, and current research on the "
         "company's interview process",
    tools=[web_search, scrape_tool, read_resume, semantic_search_resume],
    llm=gemini_llm,
    verbose=True,
    max_iter=8,
    max_rpm=10,
    respect_context_window=True,
    backstory=(
        "Your role is crucial in anticipating the dynamics of "
        "interviews. With your ability to formulate key questions "
        "and talking points, you prepare candidates for success, "
        "ensuring they can confidently address all aspects of the "
        "job they are applying for. You never rely on memory for what a "
        "company's hiring process looks like: you use the web_search tool to "
        "research the employer's actual interview rounds, formats, and "
        "recurring questions for the role, and you research the technologies "
        "in the job description that the candidate will be probed on. You "
        "attribute anything you learn from the web to the source you found it "
        "in, and when a search returns nothing useful you say so plainly "
        "rather than inventing a plausible-sounding interview process."
    )
)

if __name__ == "__main__":
    logger.info("crew.agents: Testing agents.py")
    # print("Testing LinkedIn Job Details Extraction Tool")
    # job_posting_url = "https://www.linkedin.com/jobs/view/4234610887/"
    # job_details = extract_linkedin_job_details_tool.run(url=job_posting_url)
    # print(job_details)

    # print("\nTesting GitHub Repositories Extraction Tool")
    # github_user_url = "https://github.com/arijitde92?tab=repositories"
    # github_repos = extract_github_repos_tool.run(user_url=github_user_url)
    # print(github_repo for github_repo in github_repos[:5])  # Print first 5 for verification
    # print("\nTesting GitHub Repo Search Tool")
    # for repo in github_repos[:3]:
    #     github_repo_search.add(repo=repo, content_types=['code'])
    #     github_repo_search_result = github_repo_search.run(repo)
    #     print(f"Repo: {repo}\nSearch Result: {github_repo_search_result}\n")
