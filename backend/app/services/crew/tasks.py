from crewai import Task
from app.services.crew.agents import (
    github_project_summarizer,
    profiler,
    resume_strategist,
    interview_preparer,
    extract_github_repos_tool,
    repo_content_searcher,
    read_resume,
    semantic_search_resume,
)

# ---------------------------------------------------------------------------
# NOTE: The researcher agent and research_task have been removed.
# Job details (job_description, requirements, seniority_level, etc.) are
# pre-fetched by the crew_runner using extract_linkedin_job_details() and
# injected into every task via the {job_details_json} crew input variable.
# This avoids a redundant MCP web scrape during crew execution.
#
# Tasks are built per crew run by build_tasks(include_github). When the
# applicant has NOT provided a GitHub profile, the GitHub summarizer task is
# omitted entirely (no scraping / BigQuery indexing) and the remaining tasks
# are given explicit grounding rules so the agents do NOT fabricate any
# GitHub projects, skills, or experience that is not in the resume.
#
# Building fresh Task objects per call (rather than reusing module-level
# singletons) also makes per-request output_file overrides thread-safe under
# the crew_runner's ThreadPoolExecutor.
# ---------------------------------------------------------------------------

# Description fragments toggled by include_github -------------------------------

# Profiler — step list ending differs depending on whether GitHub data exists.
_PROFILE_GITHUB_STEPS = (
    "  3. Use the GitHub projects summary from the previous task as additional context.\n"
    "  4. Synthesise all three sources into a comprehensive applicant profile."
)
_PROFILE_NO_GITHUB_STEPS = (
    "  3. Synthesise the resume and job details into a comprehensive applicant profile.\n\n"
    "IMPORTANT GROUNDING RULES:\n"
    "  - No GitHub profile was provided for this applicant.\n"
    "  - Use ONLY information that appears in the resume and the job details above.\n"
    "  - Do NOT invent, assume, or fabricate any projects, repositories, GitHub "
    "activity, employers, dates, metrics, or skills that are not present in the resume."
)

# Resume Strategist — clause naming the inputs differs depending on GitHub data.
_RESUME_GITHUB_CLAUSE = (
    "Use the applicant profile and GitHub projects summary from previous tasks, "
    "together with the job_description and requirements above, to:\n"
)
_RESUME_NO_GITHUB_CLAUSE = (
    "Use the applicant profile from the previous task, together with the "
    "job_description and requirements above, to:\n"
    "DO NOT add any experience, projects, or skills that are not already present "
    "in the applicant profile / original resume. Rephrase and reorder for fit only.\n"
)


def _github_summary_task() -> Task:
    """Task 1 — index GitHub repos and summarise the most relevant projects."""
    return Task(
        description=(
            "You have been given structured job details in JSON format:\n\n"
            "{job_details_json}\n\n"
            "Step 1 — Index the applicant's GitHub repositories:\n"
            "  Use the extract_github_repos_tool with the GitHub URL: {github_url}\n\n"
            "Step 2 — Build a search query:\n"
            "  From the job_description and requirements fields in the job details above, "
            "identify the key tech stacks, programming languages, frameworks, tools, and "
            "cloud technologies required. Formulate a concise search query from these.\n\n"
            "Step 3 — Search the vector store:\n"
            "  Use the repo_content_searcher tool with your query (input argument 'query') "
            "and pass the job_description as the 'job_description' argument.\n\n"
            "Step 4 — Summarise:\n"
            "  Analyse the returned repository content and identify the applicant's most "
            "relevant projects. Highlight tech stacks, languages, frameworks, tools, and "
            "cloud technologies that match the job requirements."
        ),
        expected_output=(
            "A concise summary of the applicant's most relevant GitHub projects, including "
            "project names, tech stacks, programming languages, frameworks, tools, and cloud "
            "technologies used. The summary should clearly connect the applicant's project "
            "experience to the specific job requirements."
        ),
        tools=[extract_github_repos_tool, repo_content_searcher],
        agent=github_project_summarizer,
        async_execution=False,
    )


def _profile_task(include_github: bool, github_task: Task | None) -> Task:
    """Task 2 — build a comprehensive applicant profile."""
    steps = _PROFILE_GITHUB_STEPS if include_github else _PROFILE_NO_GITHUB_STEPS
    grounded_in = "the resume, GitHub work, and" if include_github else "the resume and"
    return Task(
        description=(
            "Compile a detailed personal and professional profile for the applicant.\n\n"
            "Job details (for context):\n{job_details_json}\n\n"
            "Steps:\n"
            "  1. Read and semantically understand the resume from: {resume_path}\n"
            "  2. Use the job_description and requirements from the job details above "
            "to understand what the employer is looking for.\n"
            + steps
        ),
        context=[github_task] if include_github else [],
        expected_output=(
            "A comprehensive profile document that includes the applicant's skills, "
            "project experiences, contributions, interests, and communication style, "
            f"grounded in {grounded_in} aligned to the job requirements."
        ),
        tools=[read_resume, semantic_search_resume],
        agent=profiler,
        async_execution=False,
    )


def _resume_strategy_task(include_github: bool, profile_task: Task) -> Task:
    """Task 3 — tailor the resume to the job."""
    clause = _RESUME_GITHUB_CLAUSE if include_github else _RESUME_NO_GITHUB_CLAUSE
    return Task(
        description=(
            "Tailor the applicant's resume to maximise its fit for the target role.\n\n"
            "Job details (use for ATS keyword alignment):\n{job_details_json}\n\n"
            + clause +
            "  - Update every resume section (summary, work experience, skills, education).\n"
            "  - Embed relevant keywords from the job description for ATS optimisation.\n"
            "  - Quantify achievements with practical, believable numbers where appropriate.\n"
            "  - Ensure the resume catches the recruiter's eye at first glance."
        ),
        expected_output=(
            "An updated resume in Markdown format that effectively highlights the candidate's "
            "qualifications and experiences most relevant to the target role."
        ),
        output_file="{applicant_name}_{company_name}_{job_name}_resume.md",
        context=[profile_task],
        agent=resume_strategist,
        tools=[read_resume, semantic_search_resume],
        markdown=True,
        async_execution=False,
    )


def _interview_preparation_task(profile_task: Task, resume_task: Task) -> Task:
    """Task 4 — generate interview questions and talking points."""
    return Task(
        description=(
            "Create targeted interview questions and talking points for the applicant.\n\n"
            "Job details:\n{job_details_json}\n\n"
            "Use the tailored resume from the resume strategy task and the profile from "
            "the profile task to:\n"
            "  - Generate likely interview questions based on the job_description and requirements.\n"
            "  - Prepare concise talking points that highlight how the applicant's experience "
            "matches each key requirement.\n"
            "  - Help the candidate confidently address all aspects of the role."
        ),
        expected_output=(
            "A document containing key interview questions and tailored talking points that "
            "help the candidate demonstrate how their skills and experience match the role."
        ),
        output_file="interview_materials.md",
        context=[profile_task, resume_task],
        agent=interview_preparer,
        async_execution=False,
    )


def build_tasks(include_github: bool = True) -> list[Task]:
    """
    Construct a fresh, correctly-wired task list for one crew run.

    When ``include_github`` is False the GitHub summarizer task is omitted and
    the profile/resume tasks are given grounding rules so the agents do not
    fabricate GitHub-derived content.

    Return order is stable across both modes: the interview task is always the
    last element and the resume task always the second-to-last, so the runner
    can stamp per-request ``output_file`` paths via ``crew.tasks[-1]/[-2]``.
    """
    github_task = _github_summary_task() if include_github else None
    profile = _profile_task(include_github, github_task)
    resume = _resume_strategy_task(include_github, profile)
    interview = _interview_preparation_task(profile, resume)

    tasks = [profile, resume, interview]
    if include_github:
        tasks.insert(0, github_task)
    return tasks
