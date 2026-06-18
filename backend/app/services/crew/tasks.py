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
    scrape_tool,
    search_tool,
)

# ---------------------------------------------------------------------------
# NOTE: The researcher agent and research_task have been removed.
# Job details (job_description, requirements, seniority_level, etc.) are
# pre-fetched by Job_Applier.py using extract_linkedin_job_details() and
# injected into every task via the {job_details_json} crew input variable.
# This avoids a redundant MCP web scrape during crew execution.
# ---------------------------------------------------------------------------

# Task 1: GitHub Project Summarizer — index repos and find relevant projects
github_summary_task = Task(
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


# Task 2: Profiler — build a comprehensive applicant profile
profile_task = Task(
    description=(
        "Compile a detailed personal and professional profile for the applicant.\n\n"
        "Job details (for context):\n{job_details_json}\n\n"
        "Steps:\n"
        "  1. Read and semantically understand the resume from: {resume_path}\n"
        "  2. Use the job_description and requirements from the job details above "
        "to understand what the employer is looking for.\n"
        "  3. Use the GitHub projects summary from the previous task as additional context.\n"
        "  4. Synthesise all three sources into a comprehensive applicant profile."
    ),
    context=[github_summary_task],
    expected_output=(
        "A comprehensive profile document that includes the applicant's skills, "
        "project experiences, contributions, interests, and communication style, "
        "grounded in the resume, GitHub work, and aligned to the job requirements."
    ),
    tools=[read_resume, semantic_search_resume],
    agent=profiler,
    async_execution=False,
)

# Task 3: Resume Strategist — tailor the resume to the job
resume_strategy_task = Task(
    description=(
        "Tailor the applicant's resume to maximise its fit for the target role.\n\n"
        "Job details (use for ATS keyword alignment):\n{job_details_json}\n\n"
        "Use the applicant profile and GitHub projects summary from previous tasks, "
        "together with the job_description and requirements above, to:\n"
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

# Task 4: Interview Preparer — generate interview questions and talking points
interview_preparation_task = Task(
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
    context=[profile_task, resume_strategy_task],
    agent=interview_preparer,
    async_execution=False,
)
