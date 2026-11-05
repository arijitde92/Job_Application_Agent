"""
app.services.pipeline.prompts
-----------------------------
Task prompts for the pipeline's agents.

Each agent node sends ONE human message, rendered by :func:`render_task_message`
from three parts:

  1. the task *description* (step-by-step instructions),
  2. the *expected output* — the acceptance criteria for the final answer,
  3. the *context* — outputs of upstream nodes (parsed resume JSON, applicant
     profile, ...), each under a labelled heading.

Descriptions reference per-run values with ``{identifier}`` tokens (currently
only ``{job_details_json}``), filled in by :func:`interpolate`. Interpolation
replaces ONLY bare ``{identifier}`` tokens whose name is a known input, so the
JSON templates embedded below (``{"name": "", ...}``) pass through untouched —
``str.format`` would choke on them. Keep it that way: never add a bare
``{word}`` token to a description unless it is a real pipeline input.

Job details are extracted once by the runner (LinkedIn scrape or the user's
pasted description) and injected into every task, so no researcher agent is
needed.
"""

import re
from collections.abc import Mapping, Sequence

from app.services.resume_parser import PARSED_RESUME_SCHEMA

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def interpolate(text: str, inputs: Mapping[str, object]) -> str:
    """
    Replace ``{name}`` tokens in ``text`` with ``inputs[name]``.

    Tokens whose name is not in ``inputs`` — and every brace that is not a
    bare ``{identifier}`` — are left exactly as they are.

    Args:
        text: The template.
        inputs: Values for the placeholders.

    Returns:
        The interpolated text.
    """
    return _PLACEHOLDER.sub(
        lambda m: str(inputs[m.group(1)]) if m.group(1) in inputs else m.group(0),
        text,
    )


def render_task_message(
    description: str,
    expected_output: str,
    *,
    context: Sequence[tuple[str, str]] = (),
    markdown: bool = False,
) -> str:
    """
    Assemble the human message an agent receives for one task.

    Args:
        description: The (already interpolated) task description.
        expected_output: Acceptance criteria for the final answer.
        context: ``(label, content)`` pairs of upstream outputs.
        markdown: Require the final answer to be formatted in Markdown.

    Returns:
        The full task message.
    """
    parts = [
        f"Current Task: {description}",
        "This is the expected criteria for your final answer: "
        f"{expected_output}\n"
        "You MUST return the actual complete content as the final answer, "
        "not a summary.",
    ]
    if markdown:
        parts.append(
            "Your final answer MUST be formatted in Markdown syntax: use # for "
            "headers, **text** for bold, *text* for italics, - or * for "
            "bullet points, and ``` fences for code blocks."
        )
    if context:
        sections = "\n\n----------\n\n".join(
            f"### {label}\n{content}" for label, content in context
        )
        parts.append(f"This is the context you're working with:\n\n{sections}")
    return "\n\n".join(parts)


# ── Context labels (referenced by the task descriptions below) ────────────────

GITHUB_SUMMARY_LABEL = "GitHub projects summary (from the GitHub summary task)"
PARSED_RESUME_LABEL = "Parsed-resume JSON (from the resume analysis task)"
PROFILE_LABEL = "Applicant profile (from the profile task)"
TAILORED_RESUME_LABEL = "Tailored resume (from the resume strategy task)"


# ── Resume analysis ───────────────────────────────────────────────────────────

RESUME_ANALYSIS_EXPECTED_OUTPUT = (
    "The complete parsed-resume JSON document, exactly as successfully "
    "persisted via the save_parsed_resume tool."
)


def resume_analysis_description(resume_id: int) -> str:
    """
    Resume analysis task — extract the resume into the parsed-resume JSON and
    persist it via the save_parsed_resume tool.

    Args:
        resume_id: Database id of the resume; embedded in the schema template.
    """
    schema = PARSED_RESUME_SCHEMA.replace("<resume_id>", str(resume_id))
    return (
        "Carefully scan and analyze the applicant's resume and extract every "
        "piece of relevant information into a structured JSON document.\n\n"
        "Step 1 — Read the resume:\n"
        "  Read the full resume text with the read_resume tool.\n\n"
        "Step 2 — Extract the information into EXACTLY this JSON structure. "
        "Keep every key. When the resume does not state a value, keep the "
        "empty default shown (\"\", null, [], false or 0). Do not add keys.\n\n"
        + schema +
        "\n\nExtraction rules:\n"
        f"  - \"id\" MUST be {resume_id}.\n"
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
        "\"Multi Agent Orchestration (LangGraph, Google ADK)\" -> \"LangGraph\", "
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
    )


# ── GitHub summary ────────────────────────────────────────────────────────────

GITHUB_SUMMARY_DESCRIPTION = (
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
)

GITHUB_SUMMARY_EXPECTED_OUTPUT = (
    "A JSON object with a \"chosen_github_repos\" list, one entry per relevant "
    "repository, each carrying its \"github_repo_name\", the \"relevant_skills\" "
    "it demonstrates that the job requires, and a \"justification\" connecting "
    "that project experience to the specific job requirements."
)


# ── Profile ───────────────────────────────────────────────────────────────────

# Step list ending differs depending on whether GitHub data exists.
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


def profile_description(include_github: bool) -> str:
    """Profile task — build a comprehensive applicant profile."""
    steps = _PROFILE_GITHUB_STEPS if include_github else _PROFILE_NO_GITHUB_STEPS
    return (
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
        "the raw resume with the read_resume tool.\n"
        + steps
    )


def profile_expected_output(include_github: bool) -> str:
    """Acceptance criteria for the profile task."""
    grounded_in = "the resume, GitHub work, and" if include_github else "the resume and"
    return (
        "A comprehensive profile document that includes the applicant's skills, "
        "project experiences, contributions, interests, and communication style, "
        f"grounded in {grounded_in} aligned to the job requirements."
    )


# ── Resume strategy ───────────────────────────────────────────────────────────

# Clause naming the inputs differs depending on GitHub data.
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

# The .docx generation step, appended only when the run has a
# ResumeDocxContext (the web app). The standalone CLI builds no context, gets
# no generate_resume_docx tool, and must not be instructed to call one.
_RESUME_DOCX_STEP = (
    "\nAfter the tailored content is ready, render it as a polished Word "
    "document:\n"
    "  1. Build ONE compact JSON object (single line, no markdown fences, no "
    "comments, no trailing commas) containing ONLY the tailored resume "
    "content, with EXACTLY these top-level keys — omit any section the "
    "applicant has no content for:\n"
    "{\n"
    "  \"personal_information\": {\"name\": \"\", \"location\": \"\", "
    "\"email\": \"\", \"phone\": \"\", \"github\": \"\", \"linkedin\": \"\"},\n"
    "  \"summary\": \"\",\n"
    "  \"experience\": [{\"job_title\": \"\", \"company\": \"\", "
    "\"location\": \"\", \"work_mode\": \"\", \"start_date\": \"\", "
    "\"end_date\": \"\", \"bullets\": [\"\"]}],\n"
    "  \"education\": [{\"degree\": \"\", \"institution\": \"\", "
    "\"location\": \"\", \"gpa\": \"\", \"start_date\": \"\", "
    "\"end_date\": \"\"}],\n"
    "  \"skills\": [{\"category\": \"\", \"skills\": [\"\"]}],\n"
    "  \"publications\": [{\"citation\": \"\"}],\n"
    "  \"projects\": [{\"name\": \"\", \"url\": \"\", \"bullets\": [\"\"]}],\n"
    "  \"certifications\": [{\"name\": \"\", \"issuer\": \"\", \"year\": \"\", "
    "\"url\": \"\"}]\n"
    "}\n"
    "     Rules for this JSON:\n"
    "  - It carries the TAILORED content (the reworked summary and the "
    "reordered, keyword-aligned bullets), not the original resume verbatim.\n"
    "  - \"skills\" is a LIST of category objects. The parsed-resume JSON "
    "stores skills as a dictionary mapping category names to skill lists — "
    "convert EACH dictionary entry into one list item whose \"category\" is "
    "the key and whose \"skills\" is the value.\n"
    "  - **bold** markers are allowed inside bullet strings to highlight key "
    "technologies.\n"
    "  - Use short uppercase month-year dates as shown on the resume (e.g. "
    "\"SEP 2025\") and \"PRESENT\" as the end_date of the current position.\n"
    "  2. Call the generate_resume_docx tool with that JSON as its "
    "resume_json argument. The metadata envelope (template, page size, "
    "document name, section order) is added automatically — do NOT include "
    "it.\n"
    "  3. If the tool returns an ERROR, fix the reported problem and call it "
    "again — you have at most 3 attempts in total. After a SUCCESS, or an "
    "ERROR that tells you to stop, do NOT call the tool again.\n"
    "Whether or not the .docx was generated, your final answer MUST be the "
    "complete tailored resume in Markdown.\n"
)


def resume_strategy_description(include_github: bool, with_docx: bool) -> str:
    """
    Resume strategy task — tailor the resume to the job and, when ``with_docx``
    (the web app), render it as a .docx via the generate_resume_docx tool.

    The final answer is ALWAYS the tailored resume in Markdown — it feeds the
    interview task's context and is the fallback artifact when the document
    service is unavailable.
    """
    clause = _RESUME_GITHUB_CLAUSE if include_github else _RESUME_NO_GITHUB_CLAUSE
    description = (
        "Tailor the applicant's resume to maximise its fit for the target role.\n\n"
        "Job details (use for ATS keyword alignment):\n{job_details_json}\n\n"
        + clause +
        "  - Update every resume section (summary, work experience, skills, education).\n"
        "  - Embed relevant keywords from the job description for ATS optimisation.\n"
        "  - Quantify achievements with practical, believable numbers where appropriate.\n"
        "  - Ensure the resume catches the recruiter's eye at first glance.\n"
        "The parsed-resume JSON from the resume analysis task (in your context) "
        "is the authoritative source for personal information, education dates "
        "and institutions, publications, and certifications — copy those facts "
        "exactly and never invent contact details or URLs.\n"
    )
    if with_docx:
        description += _RESUME_DOCX_STEP
    return description


def resume_strategy_expected_output(with_docx: bool) -> str:
    """Acceptance criteria for the resume strategy task."""
    expected = (
        "An updated resume in Markdown format that effectively highlights the "
        "candidate's qualifications and experiences most relevant to the "
        "target role."
    )
    if with_docx:
        expected += (
            " When the document service is available, a polished .docx version "
            "has also been generated via the generate_resume_docx tool."
        )
    return expected


# ── Interview preparation ─────────────────────────────────────────────────────

INTERVIEW_PREPARATION_DESCRIPTION = (
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
    "Step 2 — Identify the four question categories:\n"
    "  Read the job_description and requirements above and pin down:\n"
    "    A. The MAJOR programming language of the role (the one the candidate will "
    "actually write day to day).\n"
    "    B. The MAJOR database language and/or data framework named in the job "
    "description (e.g. SQL/PostgreSQL, MongoDB, Redis, an ORM, Spark). If the job "
    "description names none, skip this category.\n"
    "    C. Cloud computing, DevOps and CI/CD — scoped to the specific cloud "
    "provider and tooling named in the job description (e.g. AWS, GCP, Azure, "
    "Docker, Kubernetes, Terraform, GitHub Actions). If no provider is named, ask "
    "generic cloud/DevOps/CI-CD questions.\n"
    "    D. The MAJOR technology or framework the role requires (e.g. React, "
    "FastAPI, Spring Boot, PyTorch, Kafka, LangChain/LangGraph).\n"
    "  Name the concrete technology you chose for each category at the top of the "
    "section, so the candidate knows what is being tested. If a category has no "
    "match in the job description, say so and redistribute its questions across "
    "the remaining categories — the totals below are fixed.\n\n"
    "Step 3 — Write the technical question bank:\n"
    "  Produce EXACTLY 10 conceptual questions and EXACTLY 10 scenario-based "
    "questions (20 in total), spread across the four categories above — aim for "
    "2-3 conceptual and 2-3 scenario questions per category, weighted towards the "
    "technologies the job description emphasises most.\n"
    "  - Conceptual questions probe understanding: how something works, why one "
    "approach is chosen over another, trade-offs, internals, common pitfalls.\n"
    "  - Scenario-based questions put the candidate in a concrete situation — a "
    "production incident, a design decision, a slow query, a failing pipeline, a "
    "scaling or migration problem — and ask what they would do.\n"
    "  - Pitch the difficulty at the seniority_level from the job details.\n"
    "  - Every question is followed immediately by its answer.\n\n"
    "  ANSWER STYLE (strict):\n"
    "  - Keep answers BRIEF — 2-4 sentences, or 3-5 short bullets. No essays, no "
    "long descriptive prose, no restating the question.\n"
    "  - Lead with the direct answer, then at most one line of justification.\n"
    "  - Add a SHORT fenced code snippet (roughly 10 lines or fewer) ONLY when code "
    "explains it faster than words — a query, a config fragment, a CLI command, a "
    "small function. Skip the snippet when prose is enough.\n"
    "  - Label each question with its category and whether it is conceptual or "
    "scenario-based.\n\n"
    "Step 4 — Build the rest of the materials:\n"
    "  Use the tailored resume from the resume strategy task, the profile from the "
    "profile task, and your research to:\n"
    "  - Add behavioural and role-fit questions drawn from the company's actual "
    "interview process where you found it in Step 1.\n"
    "  - Prepare concise talking points that highlight how the applicant's experience "
    "matches each key requirement.\n"
    "  - Help the candidate confidently address all aspects of the role.\n\n"
    "Grounding rules:\n"
    "  - Every talking point must be traceable to the applicant's profile or "
    "tailored resume. Do NOT invent experience, projects, metrics, or skills.\n"
    "  - The 20 technical questions are about the TECHNOLOGIES in the job "
    "description, not about the applicant — they may cover topics the applicant's "
    "resume does not mention, but they must never assert that the applicant has "
    "used something.\n"
    "  - Keep the two sources separate: anything taken from the web is research "
    "about the EMPLOYER, never a claim about the applicant.\n"
    "  - Cite the source URL for any specific claim about the company's interview "
    "process, and do NOT present an unsourced guess as something you found."
)

INTERVIEW_PREPARATION_EXPECTED_OUTPUT = (
    "A Markdown document containing: (1) a technical question bank of EXACTLY 10 "
    "conceptual and EXACTLY 10 scenario-based questions covering the job's major "
    "programming language, database language/framework, cloud-DevOps-CI/CD stack, "
    "and major technology/framework — each question labelled with its category and "
    "type and followed by a brief 2-4 sentence (or 3-5 bullet) answer, with a short "
    "code snippet only where it aids the explanation; (2) tailored talking points "
    "that help the candidate demonstrate how their skills and experience match the "
    "role; and (3) a short section on the company's interview process and recent "
    "context gathered via web_search, with source URLs (or an explicit note that "
    "live research was unavailable)."
)
