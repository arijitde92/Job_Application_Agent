from crewai import Task
from pydantic import BaseModel, Field

from app.services.crew.agents import (
    github_project_summarizer,
    interview_preparer,
    profiler,
    read_resume,
    resume_analyzer,
    resume_strategist,
    semantic_search_resume,
)
from app.services.crew.github_tools import (
    GithubIndexContext,
    make_extract_github_repos_tool,
    make_repo_content_searcher_tool,
)
from app.services.crew.resume_tools import (
    ResumeAnalysisContext,
    calculate_yoe,
    make_parsed_resume_guardrail,
    make_save_parsed_resume_tool,
)
from app.services.resume_parser import PARSED_RESUME_SCHEMA

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
#
# Parallelism: the GitHub summary task and the resume analysis task both run
# with async_execution=True. The (sync) profile task lists them in its
# ``context``, so CrewAI drains their futures — waiting for both and
# re-raising any failure — before profiling starts. A resume-analysis failure
# (guardrail retries exhausted) therefore aborts kickoff() before any
# downstream task runs. CrewAI validators require that the crew not END with
# more than one async task and that async tasks not reference each other in
# ``context``; the ordering below satisfies both.
# ---------------------------------------------------------------------------

# Description fragments toggled by include_github -------------------------------

# Profiler — step list ending differs depending on whether GitHub data exists.
_PROFILE_GITHUB_STEPS = (
    "  4. Use the GitHub projects summary from the GitHub summary task as additional context.\n"
    "  5. Synthesise all three sources into a comprehensive applicant profile."
)
_PROFILE_NO_GITHUB_STEPS = (
    "  4. Synthesise the parsed resume and job details into a comprehensive applicant profile.\n\n"
    "IMPORTANT GROUNDING RULES:\n"
    "  - No GitHub profile was provided for this applicant.\n"
    "  - Use ONLY information that appears in the parsed resume JSON, the raw resume, "
    "and the job details above.\n"
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


def _resume_analysis_task(ctx: ResumeAnalysisContext, *, run_async: bool = True) -> Task:
    """
    Resume analysis task — extract the resume into the parsed-resume JSON and
    persist it via the per-run save_parsed_resume tool.

    Runs with ``async_execution=True`` in the full crew so it executes in
    parallel with the GitHub summary task; the isolation test script passes
    ``run_async=False`` to run it standalone.
    """
    schema = PARSED_RESUME_SCHEMA.replace("<resume_id>", str(ctx.resume_id))
    return Task(
        # NOTE: this description embeds a JSON template. CrewAI interpolates
        # bare {identifier} tokens in descriptions — the template's braces are
        # all followed by quoted keys/whitespace so they are safe, but never
        # add a bare {word} token here unless it is a real crew input.
        description=(
            "Carefully scan and analyze the applicant's resume and extract every "
            "piece of relevant information into a structured JSON document.\n\n"
            "Step 1 — Read the resume:\n"
            "  Read the full resume text from: {resume_path}\n\n"
            "Step 2 — Extract the information into EXACTLY this JSON structure. "
            "Keep every key. When the resume does not state a value, keep the "
            "empty default shown (\"\", null, [], false or 0). Do not add keys.\n\n"
            + schema +
            "\n\nExtraction rules:\n"
            f"  - \"id\" MUST be {ctx.resume_id}.\n"
            "  - Dates use the YYYY-MM-DD format. When the resume gives only a month "
            "or year, use the first day of that month/year. A position's end_date is "
            "null when it is the applicant's current job.\n"
            "Carefully derive the accurate date format. Some may be MM/DD/YYYY or MM-DD-YYYY, some may be DD/MM/YYYY or DD-MM-YYYY, and some may be Month YYYY. Do NOT guess or invent dates.\n"
            "Always store the date in YYYY-MM-DD format.\n"
            "  - Do NOT invent, guess, or embellish anything that is not in the resume.\n"
            "  - Do NOT copy any text from the resume into the JSON that is not a direct value for a key. For example, do NOT copy a position description into the brief_summary field.\n"
            "  - Do NOT include internship experience in the parsed resume JSON if the candidate has more than one year of professional experience.\n"
            "  - Do NOT include teaching position experience in the parsed resume JSON if the candidate has more than one technical position for more than one year.\n"
            "  - Do NOT include any other position or experience like school or college position of responsibilities, volunteering, or non-technical positions in the parsed resume JSON if the candidate has more than one year of technical professional experience.\n"
            "  - Only consider a certificate or course if it has a valid certifier or issuing organization. Do NOT include any certificate or course that does not have a valid issuer.\n"
            "  - Every *_url field (linkedin_url, github_url, twitter_url, "
            "website_url, kaggle_url) MUST be a complete, valid URL — it must start "
            "with 'http://' or 'https://' and contain a domain (e.g. "
            "'https://www.linkedin.com/in/username'). Resumes often show only the "
            "clickable link text (e.g. 'LinkedIn', 'Portfolio', 'Kaggle', or a "
            "person's name) while the real URL is hidden in the hyperlink. If you "
            "can only see such link text and NOT the actual URL, leave that field "
            "as an empty string \"\". NEVER put link labels, names, or partial "
            "fragments in a URL field.\n"
            "  - Leave years_of_experience as 0 for now — you will fill it in Step 3 "
            "with the calculate_yoe tool. NEVER estimate, count, or add up the years "
            "yourself, and do NOT copy a number stated in the summary/objective.\n"
            "  - Derive has_remote_work_experience, remote_work_type, "
            "has_management_experience and management_level from the positions and "
            "their descriptions.\n"
            "  - If the country is not explicitly specified in a position or education entry, derive the country from the location information. If you are still not sure then leave it as \"\".\n"
            "  - Write a concise 2-4 sentence brief_summary of the applicant.\n"
            "  - \"skills\" is a dictionary. If the resume groups its skills under "
            "category headings (e.g. \"Languages\", \"Frameworks / Libraries\", "
            "\"Cloud & Ops\"), use each heading VERBATIM as a key and list that "
            "category's skills as an array of strings. Split parenthetical or "
            "comma-separated groupings into individual skill strings (e.g. "
            "\"Multi Agent Orchestration (Crew AI, Google ADK)\" -> \"Crew AI\", "
            "\"Google ADK\"). If the resume lists skills with NO category headings, "
            "put them all under a single \"Default\" key. Only include skills that "
            "actually appear in the resume.\n"
            "    Examples:\n"
            "      Resume text: 'Languages: Python, C++, SQL\\nCloud & Ops: GCP, AWS, "
            "Docker' -> \"skills\": {\"Languages\": [\"Python\", \"C++\", \"SQL\"], "
            "\"Cloud & Ops\": [\"GCP\", \"AWS\", \"Docker\"]}\n"
            "      Resume text: 'Programming: Python, C, C++\\nTools & Technologies: "
            "PyTorch, Git' -> \"skills\": {\"Programming\": [\"Python\", \"C\", "
            "\"C++\"], \"Tools & Technologies\": [\"PyTorch\", \"Git\"]}\n"
            "      Resume text: 'Python, HTML, CSS, JavaScript, React, AWS' -> "
            "\"skills\": {\"Default\": [\"Python\", \"HTML\", \"CSS\", "
            "\"JavaScript\", \"React\", \"AWS\"]}\n"
            "  - For position related skills, only include skills that are explicitly mentioned in the "
            "position description. Do not invent or assume any skills.\n"
            "  - The JSON must be strictly valid: no comments, no trailing commas, "
            "no markdown fences.\n\n"
            "Step 3 — MANDATORY: calculate the years of experience:\n"
            "  Call the calculate_yoe tool ONCE, passing the (start_date, end_date) "
            "pair of EVERY position you extracted in Step 2 as a list of pairs, in "
            "the same YYYY-MM-DD format you put in the JSON — for example:\n"
            "    calculate_yoe(date_ranges=[[\"2019-06-01\", \"2021-08-31\"], "
            "[\"2021-09-15\", null]])\n"
            "  Pass the end_date as null for the applicant's CURRENT position (the "
            "tool substitutes today's date for it); every other position must carry "
            "its real end_date, and at most one pair may have a null end_date. Pass "
            "ONLY professional positions — never education, project, or certification "
            "dates — pass each position exactly once, and keep them in the same order "
            "as the \"positions\" list in your JSON (the tool counts a month shared by "
            "two neighbouring positions only once).\n"
            "  Put the number the tool returns into the \"years_of_experience\" key "
            "of the JSON, exactly as returned (it is a decimal, e.g. 2.4 = 2 years "
            "and 4 months). Never compute or estimate this number yourself. If the "
            "tool reports an error, it tells you which pair is wrong — fix that pair "
            "and call it again. If the applicant has no professional positions at "
            "all, skip the tool and leave years_of_experience as 0.\n\n"
            "Step 4 — MANDATORY FINAL STEP:\n"
            "  Call the save_parsed_resume tool with the COMPLETE JSON string, "
            "including the years_of_experience value from Step 3. If it "
            "returns an ERROR, fix the reported problem and call it again with the "
            "corrected JSON. Only after it returns SUCCESS, output that same JSON as "
            "your final answer."
        ),
        expected_output=(
            "The complete parsed-resume JSON document, exactly as successfully "
            "persisted via the save_parsed_resume tool."
        ),
        tools=[read_resume, calculate_yoe, make_save_parsed_resume_tool(ctx)],
        agent=resume_analyzer,
        async_execution=run_async,
        guardrail=make_parsed_resume_guardrail(ctx),
        guardrail_max_retries=3,
    )


# ── GitHub summary task — structured output schema ───────────────────────────

class ChosenGithubRepo(BaseModel):
    """One GitHub repository picked as relevant to the target job."""

    github_repo_name: str = Field(
        description="The name of the selected GitHub repo."
    )
    relevant_skills: list[str] = Field(
        default_factory=list,
        description=(
            "The relevant skills used in this repo that match or are needed in "
            "the job description."
        ),
    )
    justification: str = Field(
        description=(
            "A justification of why this repo is chosen and how it is relevant "
            "for the given job description, explaining how the skills used in "
            "this repo are relevant for the job."
        ),
    )


class GithubReposSummary(BaseModel):
    """Structured output of the GitHub summary task."""

    chosen_github_repos: list[ChosenGithubRepo] = Field(
        default_factory=list,
        description="The applicant's repos that are most relevant to the job.",
    )


def _github_summary_task(github_ctx: GithubIndexContext) -> Task:
    """GitHub task — index GitHub repos and summarise the most relevant projects."""
    return Task(
        # NOTE: CrewAI interpolates bare {identifier} tokens in the description
        # and expected_output. Every brace in the JSON template below is
        # followed by a quoted key or whitespace, so none of them interpolate —
        # never add a bare {word} token unless it is a real crew input.
        description=(
            "You have been given structured job details in JSON format:\n\n"
            "{job_details_json}\n\n"
            "Step 1 — Index the applicant's GitHub repositories:\n"
            "  Call the github_repos_extractor tool. It takes no arguments — the "
            "applicant's GitHub profile is already configured.\n\n"
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
            "cloud technologies that match the job requirements.\n\n"
            "Step 5 — Report EXACTLY this JSON structure:\n"
            "{\n"
            "  \"chosen_github_repos\": [\n"
            "    {\n"
            "      \"github_repo_name\": \"\",\n"
            "      \"relevant_skills\": [],\n"
            "      \"justification\": \"\"\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "  - One entry per chosen repo; include only repos returned by "
            "repo_content_searcher, and use the repo name exactly as indexed.\n"
            "  - \"relevant_skills\" lists the skills, languages, frameworks, tools, and "
            "cloud technologies actually used in that repo that match or are needed in the "
            "job description — no skill the repo does not demonstrate.\n"
            "  - \"justification\" explains why the repo was chosen and how its skills map "
            "to the job requirements.\n"
            "  - Do NOT invent repositories, skills, or details that are not in the "
            "retrieved repository content.\n"
            "  - If no repo is relevant to the job, return an empty "
            "\"chosen_github_repos\" list.\n"
            "  - The JSON must be strictly valid: no comments, no trailing commas, no "
            "markdown fences."
        ),
        expected_output=(
            "A JSON object with a \"chosen_github_repos\" list, one entry per relevant "
            "repository, each carrying its \"github_repo_name\", the \"relevant_skills\" "
            "it demonstrates that the job requires, and a \"justification\" connecting "
            "that project experience to the specific job requirements."
        ),
        output_pydantic=GithubReposSummary,
        tools=[
            make_extract_github_repos_tool(github_ctx),
            make_repo_content_searcher_tool(github_ctx),
        ],
        agent=github_project_summarizer,
        # Runs in parallel with the resume analysis task; the profile task's
        # context drains both before profiling starts.
        async_execution=True,
    )


def _profile_task(
    include_github: bool, github_task: Task | None, resume_analysis_task: Task
) -> Task:
    """Profile task — build a comprehensive applicant profile."""
    steps = _PROFILE_GITHUB_STEPS if include_github else _PROFILE_NO_GITHUB_STEPS
    grounded_in = "the resume, GitHub work, and" if include_github else "the resume and"
    return Task(
        description=(
            "Compile a detailed personal and professional profile for the applicant.\n\n"
            "Job details (for context):\n{job_details_json}\n\n"
            "Steps:\n"
            "  1. Use the structured parsed-resume JSON from the resume analysis task "
            "(provided in your context) as the PRIMARY source of the applicant's "
            "details: contact info, positions, skills, education, projects, "
            "publications, certifications, and experience summary.\n"
            "  2. Use the job_description and requirements from the job details above "
            "to understand what the employer is looking for.\n"
            "  3. Only if a detail is missing or unclear in the parsed JSON, consult "
            "the raw resume at: {resume_path}\n"
            + steps
        ),
        context=(
            [github_task, resume_analysis_task]
            if include_github
            else [resume_analysis_task]
        ),
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
    """Resume strategy task — tailor the resume to the job."""
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
    """Interview task — research the employer, then generate questions and talking points."""
    return Task(
        description=(
            "Create targeted interview questions and talking points for the applicant.\n\n"
            "Job details:\n{job_details_json}\n\n"
            "Step 1 — Research the interview with the web_search tool:\n"
            "  Run 2-4 searches, ONE topic per call, using the company_name, job_name and "
            "the key technologies from the job_description above. Useful angles:\n"
            "    - \"<company_name> <job_name> interview process rounds\"\n"
            "    - \"<company_name> interview questions <key technology>\"\n"
            "    - \"<key technology / skill> interview questions <seniority_level>\"\n"
            "    - recent news about the company or the team, for the candidate's own questions.\n"
            "  If a search returns an \"ERROR: ...\" string, or the results are irrelevant, "
            "do NOT retry it more than once — carry on with the resume and job details "
            "alone and note in your output that live research was unavailable.\n\n"
            "Step 2 — Build the materials:\n"
            "  Use the tailored resume from the resume strategy task, the profile from the "
            "profile task, and your research to:\n"
            "  - Generate likely interview questions based on the job_description, the "
            "requirements, and the company's actual interview process where you found it.\n"
            "  - Prepare concise talking points that highlight how the applicant's experience "
            "matches each key requirement.\n"
            "  - Help the candidate confidently address all aspects of the role.\n\n"
            "Grounding rules:\n"
            "  - Every talking point must be traceable to the applicant's profile or "
            "tailored resume. Do NOT invent experience, projects, metrics, or skills.\n"
            "  - Keep the two sources separate: anything taken from the web is research "
            "about the EMPLOYER, never a claim about the applicant.\n"
            "  - Cite the source URL for any specific claim about the company's interview "
            "process, and do NOT present an unsourced guess as something you found."
        ),
        expected_output=(
            "A document containing key interview questions and tailored talking points that "
            "help the candidate demonstrate how their skills and experience match the role, "
            "including a short section on the company's interview process and recent context "
            "gathered via web_search, with source URLs (or an explicit note that live "
            "research was unavailable)."
        ),
        output_file="interview_materials.md",
        context=[profile_task, resume_task],
        agent=interview_preparer,
        async_execution=False,
    )


def build_tasks(
    include_github: bool = True,
    resume_ctx: ResumeAnalysisContext | None = None,
    github_ctx: GithubIndexContext | None = None,
) -> list[Task]:
    """
    Construct a fresh, correctly-wired task list for one crew run.

    Task order: [github?, resume_analysis, profile, resume, interview]. The
    GitHub summary and resume analysis tasks run with ``async_execution=True``
    (in parallel); the profile task lists them in its ``context`` and starts
    only after both succeed. If the resume analysis fails (guardrail retries
    exhausted), ``kickoff()`` raises before the profile task runs.

    When ``include_github`` is False the GitHub summarizer task is omitted and
    the profile/resume tasks are given grounding rules so the agents do not
    fabricate GitHub-derived content.

    ``resume_ctx`` carries the user/resume identity for the resume analysis
    task's save tool. When None (standalone CLI), a local-only context is used:
    the parsed JSON is written to temp storage but never uploaded to GCS or
    recorded in the database.

    ``github_ctx`` carries the applicant's GitHub identity for the indexing and
    search tools; it is required when ``include_github`` is True, because the
    vector-store search is scoped by it (an unscoped search would span every
    indexed applicant's repositories).

    Return order is stable across both modes: the interview task is always the
    last element and the resume task always the second-to-last, so the runner
    can stamp per-request ``output_file`` paths via ``crew.tasks[-1]/[-2]``.
    """
    if resume_ctx is None:
        resume_ctx = ResumeAnalysisContext(
            user_id=0, resume_id=0, user_name="cli_user", local_only=True
        )
    if include_github and github_ctx is None:
        raise ValueError(
            "build_tasks(include_github=True) requires a github_ctx — without it "
            "the repo search cannot be scoped to the applicant."
        )

    github_task = _github_summary_task(github_ctx) if include_github else None
    analysis = _resume_analysis_task(resume_ctx)
    profile = _profile_task(include_github, github_task, analysis)
    resume = _resume_strategy_task(include_github, profile)
    interview = _interview_preparation_task(profile, resume)

    tasks = [analysis, profile, resume, interview]
    if include_github:
        tasks.insert(0, github_task)
    return tasks
