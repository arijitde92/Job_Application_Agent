"""
Tests for the pipeline's LangChain agent middleware.

Each middleware is exercised inside a real ``create_agent`` loop driven by a
scripted chat model, so the assertions cover the behaviour the agents see:
error observations, the forced final answer, and the guardrail retry loop
with the faithfulness judge.
"""

import json

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.services.pipeline.guardrails import FaithfulnessJudge, FaithfulnessVerdict
from app.services.pipeline.middleware import (
    FINAL_ANSWER_NUDGE,
    MaxIterationsMiddleware,
    ParsedResumeGuardrailMiddleware,
    ResumeAnalysisError,
    ToolErrorMiddleware,
    model_turns_since_last_human,
)
from app.services.pipeline.tools import ResumeAnalysisContext, make_save_parsed_resume_tool
from tests.lc_fakes import (
    FakeStructuredChatModel,
    FakeToolCallingChatModel,
    ai_calls,
    tool_call,
)

VALID_JSON = json.dumps({"id": 3, "result": {"name": "Ada Lovelace"}})
BAD_URL_JSON = json.dumps({"id": 3, "result": {"name": "Ada", "linkedin_url": "LinkedIn"}})


@tool
def explode(reason: str) -> str:
    """Always fails."""
    raise ValueError(f"bad input: {reason}")


@tool
def lookup(term: str) -> str:
    """Look something up."""
    return f"result for {term}"


def _tool_messages(result) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


# ── ToolErrorMiddleware ───────────────────────────────────────────────────────

def test_tool_exception_becomes_error_observation():
    model = FakeToolCallingChatModel(responses=[
        ai_calls(tool_call("explode", {"reason": "nope"})),
        AIMessage(content="recovered"),
    ])
    agent = create_agent(model, [explode], middleware=[ToolErrorMiddleware()])

    result = agent.invoke({"messages": [HumanMessage("go")]})

    [observation] = _tool_messages(result)
    assert observation.status == "error"
    assert "bad input: nope" in observation.content
    assert result["messages"][-1].content == "recovered"
    # The model saw the error on its second turn.
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in model.calls[1])


def test_without_middleware_tool_exceptions_propagate():
    """Pins why ToolErrorMiddleware exists: create_agent re-raises by default."""
    model = FakeToolCallingChatModel(responses=[ai_calls(tool_call("explode", {"reason": "x"}))])
    agent = create_agent(model, [explode])
    with pytest.raises(ValueError, match="bad input"):
        agent.invoke({"messages": [HumanMessage("go")]})


# ── MaxIterationsMiddleware ───────────────────────────────────────────────────

def test_model_turns_since_last_human_counts_only_the_current_attempt():
    messages = [
        HumanMessage("task"), AIMessage("a"), AIMessage("b"),
        HumanMessage("feedback"), AIMessage("c"),
    ]
    assert model_turns_since_last_human(messages) == 1


def test_final_iteration_disables_tools_and_nudges():
    # A model that would call tools forever.
    model = FakeToolCallingChatModel(responses=[ai_calls(tool_call("lookup", {"term": "x"}))])
    agent = create_agent(model, [lookup], middleware=[MaxIterationsMiddleware(3)])

    result = agent.invoke({"messages": [HumanMessage("go")]})

    assert len(model.calls) == 3
    assert model.tool_choices[:2] == [None, None]
    assert model.tool_choices[2] == "none"
    assert isinstance(model.calls[2][-1], HumanMessage)
    assert model.calls[2][-1].content == FINAL_ANSWER_NUDGE
    # The final turn's leftover tool calls are dropped, which ends the loop.
    assert len(_tool_messages(result)) == 2
    assert result["messages"][-1].tool_calls == []


class FakeAnthropicChatModel(FakeToolCallingChatModel):
    @property
    def _llm_type(self) -> str:
        return "anthropic-chat"


def test_anthropic_models_get_native_none_tool_choice():
    """ChatAnthropic would read a bare "none" as the name of a tool to force."""
    anthropic_like = FakeAnthropicChatModel(responses=[ai_calls(tool_call("lookup", {"term": "x"}))])
    agent = create_agent(anthropic_like, [lookup], middleware=[MaxIterationsMiddleware(1)])
    agent.invoke({"messages": [HumanMessage("go")]})
    assert anthropic_like.tool_choices[0] == {"type": "none"}


def test_max_iter_must_be_positive():
    with pytest.raises(ValueError):
        MaxIterationsMiddleware(0)


# ── ParsedResumeGuardrailMiddleware ───────────────────────────────────────────

def make_ctx(resume_text: str = "Ada Lovelace") -> ResumeAnalysisContext:
    return ResumeAnalysisContext(
        user_id=1, resume_id=3, user_name="ada", local_only=True, resume_text=resume_text
    )


def run_guarded(ctx, responses, judge=None):
    model = FakeToolCallingChatModel(responses=responses)
    agent = create_agent(
        model,
        [make_save_parsed_resume_tool(ctx)],
        middleware=[ToolErrorMiddleware(), ParsedResumeGuardrailMiddleware(ctx, judge, max_retries=3)],
    )
    return model, agent.invoke({"messages": [HumanMessage("extract")]})


def save_then_answer(payload: str = VALID_JSON) -> list:
    return [
        ai_calls(tool_call("save_parsed_resume", {"parsed_resume_json": payload})),
        AIMessage(content=payload),
    ]


def judge_with(*outputs) -> FaithfulnessJudge:
    return FaithfulnessJudge(FakeStructuredChatModel(outputs=list(outputs)))


def test_guardrail_passes_after_successful_save_and_faithful_verdict():
    ctx = make_ctx()
    judge = judge_with(FaithfulnessVerdict(score=10))
    model, result = run_guarded(ctx, save_then_answer(), judge)

    assert ctx.state["saved"] is True
    assert json.loads(ctx.state["json"])["id"] == 3
    assert len(model.calls) == 2  # no retry
    assert result.get("guardrail_retries", 0) == 0


def test_guardrail_retries_with_feedback_when_nothing_was_saved():
    ctx = make_ctx()
    responses = [AIMessage(content="done without saving"), *save_then_answer()]
    model, result = run_guarded(ctx, responses, judge_with(FaithfulnessVerdict(score=9)))

    assert result["guardrail_retries"] == 1
    feedback = [m for m in model.calls[1] if isinstance(m, HumanMessage)][-1]
    assert "NOT saved" in feedback.content
    assert ctx.state["saved"] is True


def test_guardrail_raises_after_retry_budget():
    ctx = make_ctx()
    with pytest.raises(ResumeAnalysisError, match="after 3 retries"):
        run_guarded(ctx, [AIMessage(content="never saves")])


def test_guardrail_rejects_unfaithful_extraction_then_accepts_the_fix():
    ctx = make_ctx()
    judge = judge_with(
        FaithfulnessVerdict(score=4, unsupported_claims=["skills: Rust"], feedback="Remove Rust."),
        FaithfulnessVerdict(score=9),
    )
    responses = [*save_then_answer(), *save_then_answer()]
    model, result = run_guarded(ctx, responses, judge)

    assert result["guardrail_retries"] == 1
    feedback = [m for m in model.calls[2] if isinstance(m, HumanMessage)][-1].content
    assert "faithfulness score 4/10" in feedback
    assert "skills: Rust" in feedback
    assert "Remove Rust." in feedback
    assert ctx.state["saved"] is True  # re-saved after the fix


def test_unfaithful_verdict_clears_the_save():
    ctx = make_ctx()
    ctx.state.update({"saved": True, "json": VALID_JSON})
    guard = ParsedResumeGuardrailMiddleware(ctx, judge_with(FaithfulnessVerdict(score=2)))
    assert guard.check() is not None
    assert "saved" not in ctx.state


def test_judge_errors_fail_open():
    ctx = make_ctx()
    ctx.state.update({"saved": True, "json": VALID_JSON})
    guard = ParsedResumeGuardrailMiddleware(ctx, judge_with(RuntimeError("judge down")))
    assert guard.check() is None


def test_judge_is_skipped_without_reference_text():
    ctx = make_ctx(resume_text="")
    ctx.state.update({"saved": True, "json": VALID_JSON})
    judge_model = FakeStructuredChatModel(outputs=[FaithfulnessVerdict(score=0)])
    guard = ParsedResumeGuardrailMiddleware(ctx, FaithfulnessJudge(judge_model))
    assert guard.check() is None
    assert judge_model.calls == []


def test_invalid_url_in_saved_json_is_rejected():
    ctx = make_ctx()
    ctx.state.update({"saved": True, "json": BAD_URL_JSON})
    feedback = ParsedResumeGuardrailMiddleware(ctx).check()
    assert "linkedin_url" in feedback


def test_judge_threshold_boundary():
    judge = judge_with(FaithfulnessVerdict(score=7))
    assert judge.passes(FaithfulnessVerdict(score=7))
    assert not judge.passes(FaithfulnessVerdict(score=6))
