"""
End-to-end tests for the LangGraph pipeline with scripted chat models.

Every agent runs its real ``create_agent`` loop, middleware stack and tools;
only the chat models are fakes (``tests.lc_fakes``) and the GitHub network /
vector-store calls are monkeypatched — no API keys, no network.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import app.services.pipeline.tools.github_tools as github_tools
from app.services.pipeline import (
    AgentRole,
    ModelRegistry,
    ResumeAnalysisError,
    analyze_resume,
    build_pipeline,
    build_run_config,
)
from app.services.pipeline import prompts
from app.services.pipeline.graph import (
    GITHUB_SUMMARY,
    INTERVIEW_PREPARATION,
    PROFILE,
    RESUME_ANALYSIS,
    RESUME_STRATEGY,
)
from app.services.pipeline.guardrails import FaithfulnessVerdict
from app.services.pipeline.tools import (
    GithubIndexContext,
    ResumeAnalysisContext,
    ResumeDocxContext,
)
from app.services.resume_parser import ParsedResume
from tests.lc_fakes import (
    FakeStructuredChatModel,
    FakeToolCallingChatModel,
    ai_calls,
    tool_call,
)

RESUME_TEXT = "Ada Lovelace\nada@example.com\nSkills — Languages: Python, SQL\n"
PARSED_JSON = json.dumps({
    "id": 7,
    "result": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "skills": {"Languages": ["Python", "SQL"]},
    },
})
INPUTS = {
    "applicant_name": "ada",
    "job_posting_url": "https://www.linkedin.com/jobs/view/1/",
    "job_name": "Backend Engineer",
    "company_name": "Acme",
    "job_details_json": json.dumps({"job_name": "Backend Engineer", "company_name": "Acme"}),
}
SUMMARY_ARGS = {
    "chosen_github_repos": [
        {"github_repo_name": "engine", "relevant_skills": ["Python"], "justification": "Core skill."}
    ]
}


@pytest.fixture(autouse=True)
def _no_github_network(monkeypatch):
    """Stub out GitHub scraping, indexing and vector search; record search scope."""
    searches = []
    monkeypatch.setattr(
        github_tools, "_get_repository_links",
        lambda url, acc: ["https://github.com/ada/engine"],
    )
    monkeypatch.setattr(github_tools, "process_github_repo_to_vector_store", lambda *a, **k: None)

    def fake_query(query, top_k=5, repo_username=None):
        searches.append(repo_username)
        return [("def run(): ...", {"repo": "engine"})]

    monkeypatch.setattr(github_tools, "query_github_vector_store", fake_query)
    return searches


@pytest.fixture
def resume_path(tmp_path):
    path = tmp_path / "resume.md"
    path.write_text(RESUME_TEXT, encoding="utf-8")
    return str(path)


def make_ctx(resume_text: str = RESUME_TEXT) -> ResumeAnalysisContext:
    return ResumeAnalysisContext(
        user_id=1, resume_id=7, user_name="ada", local_only=True, resume_text=resume_text
    )


def analyzer_script() -> list:
    """read_resume -> calculate_yoe -> save_parsed_resume -> final answer."""
    return [
        ai_calls(tool_call("read_resume")),
        ai_calls(tool_call("calculate_yoe", {"date_ranges": []})),
        ai_calls(tool_call("save_parsed_resume", {"parsed_resume_json": PARSED_JSON})),
        AIMessage(content=PARSED_JSON),
    ]


def make_models(**overrides) -> tuple[ModelRegistry, dict]:
    fakes = {
        AgentRole.GITHUB_SUMMARIZER: FakeToolCallingChatModel(responses=[
            ai_calls(tool_call("github_repos_extractor")),
            ai_calls(tool_call("repo_content_searcher", {"query": "python", "job_description": "jd"})),
            ai_calls(tool_call("GithubReposSummary", SUMMARY_ARGS)),
        ]),
        AgentRole.RESUME_ANALYZER: FakeToolCallingChatModel(responses=analyzer_script()),
        AgentRole.FAITHFULNESS_JUDGE: FakeStructuredChatModel(
            outputs=[FaithfulnessVerdict(score=9, feedback="")]
        ),
        AgentRole.PROFILER: FakeToolCallingChatModel(responses=[
            ai_calls(tool_call("read_resume")),
            AIMessage(content="PROFILE: Ada is a Python engineer."),
        ]),
        AgentRole.RESUME_STRATEGIST: FakeToolCallingChatModel(responses=[
            AIMessage(content="# Ada Lovelace\nTailored resume"),
        ]),
        AgentRole.INTERVIEW_PREPARER: FakeToolCallingChatModel(responses=[
            AIMessage(content="# Interview materials"),
        ]),
    }
    fakes.update(overrides)
    return ModelRegistry.from_models(fakes), fakes


def _human_prompt(fake: FakeToolCallingChatModel) -> str:
    """The task message of the fake's first call."""
    return next(m.text for m in fake.calls[0] if isinstance(m, HumanMessage))


# ── Topology ──────────────────────────────────────────────────────────────────

def _edges(graph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.get_graph().edges}


def test_topology_with_github_fans_out_and_joins_at_profile(resume_path):
    models, _ = make_models()
    graph = build_pipeline(
        include_github=True, resume_path=resume_path, resume_ctx=make_ctx(),
        github_ctx=GithubIndexContext("https://github.com/ada", "ada"), models=models,
    )
    edges = _edges(graph)
    assert ("__start__", GITHUB_SUMMARY) in edges
    assert ("__start__", RESUME_ANALYSIS) in edges
    assert (GITHUB_SUMMARY, PROFILE) in edges
    assert (RESUME_ANALYSIS, PROFILE) in edges
    assert (PROFILE, RESUME_STRATEGY) in edges
    assert (RESUME_STRATEGY, INTERVIEW_PREPARATION) in edges
    assert (INTERVIEW_PREPARATION, "__end__") in edges


def test_topology_without_github_has_no_github_node(resume_path):
    models, _ = make_models()
    graph = build_pipeline(include_github=False, resume_path=resume_path, models=models)
    nodes = set(graph.get_graph().nodes)
    assert GITHUB_SUMMARY not in nodes
    assert (RESUME_ANALYSIS, PROFILE) in _edges(graph)


def test_include_github_requires_a_github_ctx(resume_path):
    with pytest.raises(ValueError, match="requires a github_ctx"):
        build_pipeline(include_github=True, resume_path=resume_path, models=make_models()[0])


# ── End-to-end runs ───────────────────────────────────────────────────────────

def test_full_run_with_github_fills_every_output(resume_path, _no_github_network):
    models, fakes = make_models()
    ctx = make_ctx()
    graph = build_pipeline(
        include_github=True, resume_path=resume_path, resume_ctx=ctx,
        github_ctx=GithubIndexContext("https://github.com/ada", "ada"), models=models,
    )

    state = graph.invoke({"inputs": INPUTS}, build_run_config(include_github=True))

    assert json.loads(state["github_summary"]) == SUMMARY_ARGS
    assert state["parsed_resume_json"] == ctx.state["json"]
    assert ParsedResume.model_validate_json(state["parsed_resume_json"]).id == 7
    assert state["applicant_profile"].startswith("PROFILE:")
    assert state["tailored_resume_md"].startswith("# Ada Lovelace")
    assert state["interview_materials_md"] == "# Interview materials"

    # The vector search was scoped to the applicant's own GitHub account.
    assert _no_github_network == ["ada"]

    # Upstream outputs reach downstream prompts, with job details interpolated.
    profile_prompt = _human_prompt(fakes[AgentRole.PROFILER])
    assert prompts.GITHUB_SUMMARY_LABEL in profile_prompt
    assert prompts.PARSED_RESUME_LABEL in profile_prompt
    assert "{job_details_json}" not in profile_prompt
    assert "Backend Engineer" in profile_prompt
    interview_prompt = _human_prompt(fakes[AgentRole.INTERVIEW_PREPARER])
    assert "Tailored resume" in interview_prompt
    assert "PROFILE: Ada" in interview_prompt


def test_run_without_github_uses_grounding_rules(resume_path):
    models, fakes = make_models()
    graph = build_pipeline(
        include_github=False, resume_path=resume_path, resume_ctx=make_ctx(), models=models,
    )

    state = graph.invoke({"inputs": INPUTS}, build_run_config(include_github=False))

    assert "github_summary" not in state
    assert state["interview_materials_md"] == "# Interview materials"
    assert fakes[AgentRole.GITHUB_SUMMARIZER].calls == []
    profile_prompt = _human_prompt(fakes[AgentRole.PROFILER])
    assert "No GitHub profile was provided" in profile_prompt
    assert prompts.GITHUB_SUMMARY_LABEL not in profile_prompt


def test_strategist_gets_docx_tool_only_with_docx_ctx(resume_path, tmp_path):
    models, fakes = make_models()
    docx_ctx = ResumeDocxContext(
        applicant_name="ada", company_name="Acme", job_name="Eng", job_id=1,
        output_path=str(tmp_path / "out.docx"),
    )
    graph = build_pipeline(
        include_github=False, resume_path=resume_path, resume_ctx=make_ctx(),
        docx_ctx=docx_ctx, models=models,
    )
    graph.invoke({"inputs": INPUTS}, build_run_config(include_github=False))

    strategist = fakes[AgentRole.RESUME_STRATEGIST]
    assert "generate_resume_docx" in strategist.bound_tool_names[0]
    assert "generate_resume_docx" in _human_prompt(strategist)


def test_resume_analysis_failure_aborts_before_profile(resume_path):
    # The analyzer never calls save_parsed_resume: the guardrail sends it back
    # three times, then aborts the whole run.
    never_saves = FakeToolCallingChatModel(responses=[AIMessage(content="I am done.")])
    models, fakes = make_models(**{AgentRole.RESUME_ANALYZER: never_saves})
    graph = build_pipeline(
        include_github=False, resume_path=resume_path, resume_ctx=make_ctx(), models=models,
    )

    with pytest.raises(ResumeAnalysisError, match="after 3 retries"):
        graph.invoke({"inputs": INPUTS}, build_run_config(include_github=False))

    assert len(never_saves.calls) == 4  # first attempt + 3 retries
    assert fakes[AgentRole.PROFILER].calls == []


def test_analyze_resume_standalone_returns_the_saved_json(resume_path):
    models, _ = make_models()
    ctx = make_ctx()
    result = analyze_resume(ctx, resume_path, models=models)
    assert result == ctx.state["json"]
    assert json.loads(result)["result"]["name"] == "Ada Lovelace"


def test_streamed_updates_name_each_node(resume_path):
    models, _ = make_models()
    graph = build_pipeline(
        include_github=True, resume_path=resume_path, resume_ctx=make_ctx(),
        github_ctx=GithubIndexContext("https://github.com/ada", "ada"), models=models,
    )
    nodes = [
        node
        for chunk in graph.stream({"inputs": INPUTS}, build_run_config(include_github=True),
                                  stream_mode="updates")
        for node in chunk
    ]
    assert set(nodes[:2]) == {GITHUB_SUMMARY, RESUME_ANALYSIS}
    assert nodes[2:] == [PROFILE, RESUME_STRATEGY, INTERVIEW_PREPARATION]
