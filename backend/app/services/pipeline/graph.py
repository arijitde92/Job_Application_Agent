"""
app.services.pipeline.graph
---------------------------
The job-application pipeline as a LangGraph ``StateGraph``.

    START ──┬──> github_summary ───┐
            │                      ├──> profile ──> resume_strategy ──> interview_preparation ──> END
            └──> resume_analysis ──┘

``github_summary`` and ``resume_analysis`` run in parallel; LangGraph starts
``profile`` only after both have finished, and an exception in either branch
(e.g. the resume-analysis guardrail giving up) aborts the run before any
downstream node starts. When the applicant has no GitHub profile the
``github_summary`` node is left out entirely and the remaining prompts carry
explicit grounding rules so the agents do not fabricate GitHub-derived content.

Each node runs one agent from ``app.services.pipeline.agents`` on a single
task message (``app.services.pipeline.prompts``) and writes its result to its
own state key, so the parallel branches never write the same key.

Usage::

    graph = build_pipeline(include_github=False, resume_path=path)
    final_state = graph.invoke({"inputs": inputs}, build_run_config(include_github=False))
    final_state["tailored_resume_md"], final_state["interview_materials_md"]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langgraph.graph import END, START, StateGraph

from app.core.logging import get_logger
from app.services.pipeline import prompts
from app.services.pipeline.agents import (
    build_github_summarizer_agent,
    build_interview_preparer_agent,
    build_profiler_agent,
    build_resume_analyzer_agent,
    build_resume_strategist_agent,
)
from app.services.pipeline.llms import ModelRegistry, default_registry
from app.services.pipeline.schemas import GithubReposSummary
from app.services.pipeline.tracing import RECURSION_LIMIT
from app.services.pipeline.tools import (
    GithubIndexContext,
    ResumeAnalysisContext,
    ResumeDocxContext,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = get_logger(__name__)

# Node names (also the step names in traces and the run log).
GITHUB_SUMMARY = "github_summary"
RESUME_ANALYSIS = "resume_analysis"
PROFILE = "profile"
RESUME_STRATEGY = "resume_strategy"
INTERVIEW_PREPARATION = "interview_preparation"
NODE_NAMES = (GITHUB_SUMMARY, RESUME_ANALYSIS, PROFILE, RESUME_STRATEGY, INTERVIEW_PREPARATION)


class PipelineInputs(TypedDict):
    """Per-run values interpolated into the task prompts."""

    applicant_name: str
    job_posting_url: str
    job_name: str
    company_name: str
    job_details_json: str
    github_url: NotRequired[str]


class PipelineState(TypedDict, total=False):
    """Graph state; each node writes only its own output key."""

    inputs: PipelineInputs
    github_summary: str
    parsed_resume_json: str
    applicant_profile: str
    tailored_resume_md: str
    interview_materials_md: str


class PipelineError(RuntimeError):
    """A pipeline node finished without the output it is required to produce."""


def _run_agent(agent: "CompiledStateGraph", message: str, config: RunnableConfig) -> dict[str, Any]:
    """
    Invoke ``agent`` on one task message.

    The node's config is passed through so the agent's run nests under the
    node in traces and inherits its callbacks; the recursion limit is raised
    to fit a full agent loop (see ``tracing.RECURSION_LIMIT``).
    """
    return agent.invoke(
        {"messages": [HumanMessage(message)]},
        patch_config(config, recursion_limit=RECURSION_LIMIT),
    )


def _final_text(result: dict[str, Any]) -> str:
    """The text of the agent's last message."""
    return result["messages"][-1].text


def analyze_resume(
    resume_ctx: ResumeAnalysisContext,
    resume_path: str,
    *,
    models: ModelRegistry | None = None,
    config: RunnableConfig | None = None,
) -> str:
    """
    Run the Resume Analyzer on its own (the ``resume_analysis`` node's logic).

    Also used by the isolation script and the RAGAS harness, so they evaluate
    exactly the agent, prompt and guardrail the pipeline runs.

    Args:
        resume_ctx: Identity and shared state; ``state["json"]`` holds the
            result afterwards.
        resume_path: The extracted resume text file.
        models: Model registry (defaults to the production models).
        config: Optional run config.

    Returns:
        The normalized parsed-resume JSON, exactly as saved.

    Raises:
        ResumeAnalysisError: The guardrail retries were exhausted.
        PipelineError: The agent finished without a successful save.
    """
    agent = build_resume_analyzer_agent(resume_ctx, resume_path, models or default_registry())
    message = prompts.render_task_message(
        prompts.resume_analysis_description(resume_ctx.resume_id),
        prompts.RESUME_ANALYSIS_EXPECTED_OUTPUT,
    )
    _run_agent(agent, message, config)
    if not (resume_ctx.state.get("saved") and resume_ctx.state.get("json")):
        raise PipelineError("The resume analyzer finished without saving a parsed resume.")
    return resume_ctx.state["json"]


def build_pipeline(
    *,
    include_github: bool,
    resume_path: str,
    resume_ctx: ResumeAnalysisContext | None = None,
    github_ctx: GithubIndexContext | None = None,
    docx_ctx: ResumeDocxContext | None = None,
    models: ModelRegistry | None = None,
) -> "CompiledStateGraph":
    """
    Compile the job-application graph for one run.

    Args:
        include_github: Include the GitHub summarizer node. When False the
            run tailors from the resume + job details alone, under explicit
            grounding rules.
        resume_path: The extracted resume text (.md) file the agents'
            ``read_resume`` tool reads.
        resume_ctx: Identity/state for the resume analyzer's save tool and
            guardrail. When None (standalone CLI) a local-only context is used:
            the parsed JSON is written to temp storage but never uploaded to
            GCS or recorded in the database, and the faithfulness judge is off.
        github_ctx: The applicant's GitHub identity, closured into the
            indexing and search tools so the vector search is scoped to their
            own repositories. Required when ``include_github`` is True.
        docx_ctx: Job identity + output path for the resume strategist's
            generate_resume_docx tool. When None (standalone CLI) the tool is
            not attached and the strategist produces the Markdown resume only.
        models: Model registry (defaults to the production models).

    Returns:
        The compiled graph. Invoke it with ``{"inputs": PipelineInputs}``.

    Raises:
        ValueError: ``include_github`` is True but ``github_ctx`` is None — an
            unscoped search would span every indexed applicant's repositories.
    """
    if include_github and github_ctx is None:
        raise ValueError(
            "build_pipeline(include_github=True) requires a github_ctx — without it "
            "the repo search cannot be scoped to the applicant."
        )
    if resume_ctx is None:
        resume_ctx = ResumeAnalysisContext(
            user_id=0, resume_id=0, user_name="cli_user", local_only=True
        )
    models = models or default_registry()
    with_docx = docx_ctx is not None

    # Agents are compiled once per run, with this run's tools.
    profiler = build_profiler_agent(resume_path, models)
    strategist = build_resume_strategist_agent(resume_path, models, docx_ctx)
    interview_preparer = build_interview_preparer_agent(resume_path, models)

    def github_summary(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        agent = build_github_summarizer_agent(github_ctx, models)
        message = prompts.render_task_message(
            prompts.interpolate(prompts.GITHUB_SUMMARY_DESCRIPTION, state["inputs"]),
            prompts.GITHUB_SUMMARY_EXPECTED_OUTPUT,
        )
        result = _run_agent(agent, message, config)
        summary = result.get("structured_response")
        if not isinstance(summary, GithubReposSummary):
            logger.warning("graph: GitHub summarizer returned no structured summary")
            summary = GithubReposSummary()
        return {"github_summary": summary.model_dump_json(indent=2)}

    def resume_analysis(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        return {"parsed_resume_json": analyze_resume(resume_ctx, resume_path, models=models, config=config)}

    def profile(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        context = [(prompts.PARSED_RESUME_LABEL, state["parsed_resume_json"])]
        if include_github:
            context.insert(0, (prompts.GITHUB_SUMMARY_LABEL, state["github_summary"]))
        message = prompts.render_task_message(
            prompts.interpolate(prompts.profile_description(include_github), state["inputs"]),
            prompts.profile_expected_output(include_github),
            context=context,
        )
        return {"applicant_profile": _final_text(_run_agent(profiler, message, config))}

    def resume_strategy(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        context = [
            (prompts.PROFILE_LABEL, state["applicant_profile"]),
            (prompts.PARSED_RESUME_LABEL, state["parsed_resume_json"]),
        ]
        if include_github:
            context.insert(1, (prompts.GITHUB_SUMMARY_LABEL, state["github_summary"]))
        message = prompts.render_task_message(
            prompts.interpolate(
                prompts.resume_strategy_description(include_github, with_docx), state["inputs"]
            ),
            prompts.resume_strategy_expected_output(with_docx),
            context=context,
            markdown=True,
        )
        return {"tailored_resume_md": _final_text(_run_agent(strategist, message, config))}

    def interview_preparation(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        message = prompts.render_task_message(
            prompts.interpolate(prompts.INTERVIEW_PREPARATION_DESCRIPTION, state["inputs"]),
            prompts.INTERVIEW_PREPARATION_EXPECTED_OUTPUT,
            context=[
                (prompts.PROFILE_LABEL, state["applicant_profile"]),
                (prompts.TAILORED_RESUME_LABEL, state["tailored_resume_md"]),
            ],
            markdown=True,
        )
        return {"interview_materials_md": _final_text(_run_agent(interview_preparer, message, config))}

    builder = StateGraph(PipelineState)
    builder.add_node(RESUME_ANALYSIS, resume_analysis)
    builder.add_node(PROFILE, profile)
    builder.add_node(RESUME_STRATEGY, resume_strategy)
    builder.add_node(INTERVIEW_PREPARATION, interview_preparation)

    builder.add_edge(START, RESUME_ANALYSIS)
    if include_github:
        builder.add_node(GITHUB_SUMMARY, github_summary)
        builder.add_edge(START, GITHUB_SUMMARY)
        # A list of sources = fan-in: profile waits for BOTH branches.
        builder.add_edge([GITHUB_SUMMARY, RESUME_ANALYSIS], PROFILE)
    else:
        builder.add_edge(RESUME_ANALYSIS, PROFILE)
    builder.add_edge(PROFILE, RESUME_STRATEGY)
    builder.add_edge(RESUME_STRATEGY, INTERVIEW_PREPARATION)
    builder.add_edge(INTERVIEW_PREPARATION, END)
    return builder.compile(name="job_application_graph")
