"""
app.services.pipeline.agents
----------------------------
The pipeline's five agents, as LangChain 1.x ``create_agent`` graphs.

Each agent is described by an :class:`AgentSpec` (role, goal, backstory, step
budget) that becomes its system prompt. :func:`build_agent` wires a spec to a
chat model, its tools, and the shared middleware stack:

  1. ``ToolErrorMiddleware``      — tool exceptions become error observations.
  2. ``MaxIterationsMiddleware``  — step budget; the last step forces a final answer.
  3. ``SummarizationMiddleware``  — summarizes the conversation before it
     outgrows the model's context window.
  4. agent-specific middleware (the resume-analysis guardrail).

Agents are built fresh per pipeline run with that run's tools — the identity
of the applicant lives in the tools (see ``app.services.pipeline.tools``),
never in shared state — so concurrent runs can never see each other's data.
The chat models themselves are shared, cached per role by
``app.services.pipeline.llms.ModelRegistry``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain.agents.structured_output import ToolStrategy

from app.services.pipeline.guardrails import FaithfulnessJudge
from app.services.pipeline.llms import AgentRole, ModelRegistry, summarization_trigger_tokens
from app.services.pipeline.middleware import (
    MaxIterationsMiddleware,
    ParsedResumeGuardrailMiddleware,
    ToolErrorMiddleware,
)
from app.services.pipeline.schemas import GithubReposSummary
from app.services.pipeline.tools import (
    GithubIndexContext,
    ResumeAnalysisContext,
    ResumeDocxContext,
    calculate_yoe,
    make_extract_github_repos_tool,
    make_generate_resume_docx_tool,
    make_read_resume_tool,
    make_repo_content_searcher_tool,
    make_save_parsed_resume_tool,
    scrape_website,
    web_search,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph

# Guardrail retries the resume analyzer gets after its first attempt.
RESUME_GUARDRAIL_MAX_RETRIES = 3


@dataclass(frozen=True)
class AgentSpec:
    """
    Static description of one agent.

    Attributes:
        name: Agent (and trace span) name.
        role: Role title.
        goal: What the agent is trying to achieve.
        backstory: Persona and working rules.
        max_iter: Maximum model calls per attempt.
    """

    name: AgentRole
    role: str
    goal: str
    backstory: str
    max_iter: int = 8

    @property
    def system_prompt(self) -> str:
        """The agent's system prompt."""
        return f"You are {self.role}. {self.backstory}\nYour personal goal is: {self.goal}"


GITHUB_PROJECT_SUMMARIZER = AgentSpec(
    name=AgentRole.GITHUB_SUMMARIZER,
    role="GitHub Project Summarizer",
    goal="Summarize the user's most relevant GitHub projects for a job application, highlighting tech stacks, languages, frameworks, tools, and cloud technologies used.",
    backstory=(
        "You are an expert in analyzing GitHub repositories and summarizing project experience for job applications. "
        "You first ensure the user's repositories are indexed in the vector store using the github_repos_extractor tool. "
        "Then, you use the repo_content_searcher tool to find the most relevant project content based on the job description and resume. "
        "Finally, you create a concise summary of the user's relevant GitHub projects, highlighting the tech stacks, programming languages, frameworks, tools, and any cloud computing technologies used."
    ),
)

RESUME_ANALYZER = AgentSpec(
    name=AgentRole.RESUME_ANALYZER,
    role="Resume Analyzer",
    goal=(
        "Carefully scan a resume and extract every piece of relevant applicant "
        "information into a strictly valid JSON document matching the required "
        "schema, using the calculate_yoe tool to derive the applicant's total "
        "years of experience from their position dates."
    ),
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
    ),
)

PROFILER = AgentSpec(
    name=AgentRole.PROFILER,
    role="Personal Profiler for Engineers",
    goal="Do incredible analytical research on job applicants to help them stand out in the job market",
    backstory=(
        "Equipped with analytical prowess, you dissect and synthesize information "
        "from diverse sources to craft comprehensive personal and professional profiles,"
        "laying the groundwork for personalized resume enhancements."
    ),
)

# max_iter leaves headroom for up to 3 docx attempts on top of the read /
# search calls and the final Markdown answer.
RESUME_STRATEGIST = AgentSpec(
    name=AgentRole.RESUME_STRATEGIST,
    role="Resume Strategist for Engineers",
    goal=(
        "Find all the best ways to make a resume stand out in the job market, "
        "and deliver it as a polished, professionally formatted .docx document "
        "via the document-generation service."
    ),
    backstory=(
        "With a strategic mind and an eye for detail, you "
        "excel at refining resumes to highlight the most "
        "relevant skills and experiences, ensuring they "
        "resonate perfectly with the job's requirements. "
        "Once the tailored content is ready, you assemble it into a structured "
        "resume JSON and call the generate_resume_docx tool to render a "
        "polished Word document. When the tool reports an ERROR you fix the "
        "input and try again, but never more than 3 attempts in total; if the "
        "service stays unavailable you fall back gracefully. Whether or not "
        "the .docx was generated, your final answer is always the complete "
        "tailored resume in Markdown."
    ),
    max_iter=10,
)

INTERVIEW_PREPARER = AgentSpec(
    name=AgentRole.INTERVIEW_PREPARER,
    role="Engineering Interview Preparer",
    goal="Create interview questions and talking points "
         "based on the resume, job requirements, and current research on the "
         "company's interview process",
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
    ),
)


def build_agent(
    spec: AgentSpec,
    model: "BaseChatModel",
    tools: Sequence["BaseTool"],
    *,
    extra_middleware: Sequence[AgentMiddleware] = (),
    response_format: Any = None,
) -> "CompiledStateGraph":
    """
    Compile one agent from its spec.

    Args:
        spec: The agent's role, goal, backstory and step budget.
        model: The chat model to run on.
        tools: The tools the agent may call.
        extra_middleware: Agent-specific middleware, appended after the
            shared stack.
        response_format: Optional structured-output strategy.

    Returns:
        The compiled agent graph; invoke it with ``{"messages": [...]}``.
    """
    middleware: list[AgentMiddleware] = [
        ToolErrorMiddleware(),
        MaxIterationsMiddleware(spec.max_iter),
        SummarizationMiddleware(
            model=model,
            trigger=("tokens", summarization_trigger_tokens(model)),
            keep=("messages", 20),
        ),
        *extra_middleware,
    ]
    return create_agent(
        model,
        list(tools),
        system_prompt=spec.system_prompt,
        middleware=middleware,
        response_format=response_format,
        name=str(spec.name),
    )


# ── Per-agent builders (one per pipeline node) ────────────────────────────────

def build_github_summarizer_agent(
    github_ctx: GithubIndexContext, models: ModelRegistry
) -> "CompiledStateGraph":
    """
    The GitHub Project Summarizer: index the applicant's repos, search them,
    and answer with a structured :class:`GithubReposSummary`. ``ToolStrategy``
    (a synthetic answer tool) is used rather than provider-native structured
    output, which Z.ai's OpenAI-compatible endpoint does not guarantee.
    """
    return build_agent(
        GITHUB_PROJECT_SUMMARIZER,
        models.get(AgentRole.GITHUB_SUMMARIZER),
        [make_extract_github_repos_tool(github_ctx), make_repo_content_searcher_tool(github_ctx)],
        response_format=ToolStrategy(GithubReposSummary),
    )


def build_resume_analyzer_agent(
    resume_ctx: ResumeAnalysisContext, resume_path: str, models: ModelRegistry
) -> "CompiledStateGraph":
    """
    The Resume Analyzer: extract the resume into the parsed-resume JSON and
    save it. Guarded by :class:`ParsedResumeGuardrailMiddleware` with the
    Claude faithfulness judge (skipped when ``resume_ctx.resume_text`` is empty).
    """
    judge = (
        FaithfulnessJudge(models.get(AgentRole.FAITHFULNESS_JUDGE))
        if resume_ctx.resume_text else None
    )
    return build_agent(
        RESUME_ANALYZER,
        models.get(AgentRole.RESUME_ANALYZER),
        [make_read_resume_tool(resume_path), calculate_yoe, make_save_parsed_resume_tool(resume_ctx)],
        extra_middleware=[
            ParsedResumeGuardrailMiddleware(
                resume_ctx, judge, max_retries=RESUME_GUARDRAIL_MAX_RETRIES
            ),
        ],
    )


def build_profiler_agent(resume_path: str, models: ModelRegistry) -> "CompiledStateGraph":
    """The Profiler: synthesize the applicant profile."""
    return build_agent(PROFILER, models.get(AgentRole.PROFILER), [make_read_resume_tool(resume_path)])


def build_resume_strategist_agent(
    resume_path: str, models: ModelRegistry, docx_ctx: ResumeDocxContext | None = None
) -> "CompiledStateGraph":
    """
    The Resume Strategist: tailor the resume. With a ``docx_ctx`` (the web
    app) it also gets the generate_resume_docx tool.
    """
    tools: list["BaseTool"] = [scrape_website, web_search, make_read_resume_tool(resume_path)]
    if docx_ctx is not None:
        tools.append(make_generate_resume_docx_tool(docx_ctx))
    return build_agent(RESUME_STRATEGIST, models.get(AgentRole.RESUME_STRATEGIST), tools)


def build_interview_preparer_agent(resume_path: str, models: ModelRegistry) -> "CompiledStateGraph":
    """
    The Interview Preparer: research the employer and write the interview
    materials. ``web_search`` never raises — this is the last node, and a
    search outage must not sink a run whose resume is already tailored.
    """
    return build_agent(
        INTERVIEW_PREPARER,
        models.get(AgentRole.INTERVIEW_PREPARER),
        [web_search, scrape_website, make_read_resume_tool(resume_path)],
    )
