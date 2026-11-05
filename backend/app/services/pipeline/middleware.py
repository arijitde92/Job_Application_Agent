"""
app.services.pipeline.middleware
--------------------------------
LangChain agent middleware shared by the pipeline's agents.

  - :class:`ToolErrorMiddleware` — turns a tool exception into an error
    ``ToolMessage`` the model can read and react to, instead of letting it
    crash the run. ``create_agent``'s ToolNode only recovers from argument
    validation errors by default; this covers everything else (a bad date
    passed to ``calculate_yoe``, an unexpected network error, ...).
  - :class:`MaxIterationsMiddleware` — caps an agent's reasoning loop. On the
    last allowed model call it disables tool use and tells the model to give
    its final answer, so the run ends with a real answer, not a cut-off.
  - :class:`ParsedResumeGuardrailMiddleware` — validates the resume
    analyzer's work once it stops (saved? valid URLs? faithful to the
    resume?) and sends it back with feedback when a check fails, up to a
    retry budget.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, NotRequired

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from pydantic import ValidationError

from app.core.logging import get_logger
from app.services.resume_parser import ParsedResume, find_invalid_url_fields

if TYPE_CHECKING:
    from langgraph.runtime import Runtime
    from langgraph.types import Command

    from app.services.pipeline.guardrails import FaithfulnessJudge
    from app.services.pipeline.tools.resume_tools import ResumeAnalysisContext

logger = get_logger(__name__)


# ── Tool errors ───────────────────────────────────────────────────────────────

class ToolErrorMiddleware(AgentMiddleware):
    """Convert tool exceptions into ``ToolMessage(status="error")`` observations."""

    @staticmethod
    def _error_message(request: ToolCallRequest, exc: Exception) -> ToolMessage:
        name = request.tool_call["name"]
        logger.warning("middleware: tool '%s' raised %s: %s", name, type(exc).__name__, exc)
        return ToolMessage(
            content=f"ERROR: {exc}",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """Run the tool; on an exception, return it as an error observation."""
        try:
            return handler(request)
        except GraphBubbleUp:  # interrupts / control flow must propagate
            raise
        except Exception as exc:  # noqa: BLE001 — every failure goes back to the model
            return self._error_message(request, exc)

    async def awrap_tool_call(self, request, handler):
        """Async counterpart of :meth:`wrap_tool_call`."""
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._error_message(request, exc)


# ── Iteration cap ─────────────────────────────────────────────────────────────

FINAL_ANSWER_NUDGE = (
    "You have reached the maximum number of steps. Now it's time you MUST give "
    "your absolute best final answer. Stop using any tools and return your "
    "absolute BEST final answer, based on everything you have gathered so far."
)


def model_turns_since_last_human(messages: Sequence[AnyMessage]) -> int:
    """Count AI messages after the most recent human message."""
    turns = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage):
            turns += 1
    return turns


def _disable_tools(request: ModelRequest) -> ModelRequest:
    """
    Return ``request`` with tool use disabled for this one model call.

    With a structured ``response_format`` the user tools are removed (the
    structured-output tool is bound separately and stays callable). Otherwise
    the tools stay bound — conversation history already contains tool calls,
    which some providers refuse to accept without tool definitions — and
    ``tool_choice`` is set to "none". Anthropic takes its native
    ``{"type": "none"}`` form: ``ChatAnthropic.bind_tools`` would read a bare
    "none" as the name of a tool to force.
    """
    if request.response_format is not None:
        return request.override(tools=[])
    llm_type = getattr(request.model, "_llm_type", "")
    tool_choice: Any = {"type": "none"} if llm_type.startswith("anthropic") else "none"
    return request.override(tool_choice=tool_choice)


class MaxIterationsMiddleware(AgentMiddleware):
    """
    Allow at most ``max_iter`` model calls per agent attempt.

    An "attempt" starts at the latest human message, so a guardrail retry
    (which appends human feedback) gets a fresh budget. The last allowed call
    runs with tool use disabled plus :data:`FINAL_ANSWER_NUDGE`; if the model
    still emits tool calls they are dropped, which ends the agent loop.

    Args:
        max_iter: Maximum model calls per attempt (>= 1).
    """

    def __init__(self, max_iter: int) -> None:
        super().__init__()
        if max_iter < 1:
            raise ValueError("max_iter must be >= 1")
        self.max_iter = max_iter

    def _is_final_turn(self, request: ModelRequest) -> bool:
        return model_turns_since_last_human(request.messages) >= self.max_iter - 1

    def _final_request(self, request: ModelRequest) -> ModelRequest:
        logger.info("middleware: iteration cap (%d) reached — forcing a final answer", self.max_iter)
        request = _disable_tools(request)
        return request.override(messages=[*request.messages, HumanMessage(FINAL_ANSWER_NUDGE)])

    @staticmethod
    def _drop_tool_calls(request: ModelRequest, response: ModelResponse) -> ModelResponse:
        if request.response_format is not None:
            return response  # the structured-output tool call IS the answer
        result = [
            m.model_copy(update={"tool_calls": [], "invalid_tool_calls": []})
            if isinstance(m, AIMessage) and m.tool_calls else m
            for m in response.result
        ]
        return ModelResponse(result=result, structured_response=response.structured_response)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Pass through, except on the final allowed call."""
        if not self._is_final_turn(request):
            return handler(request)
        return self._drop_tool_calls(request, handler(self._final_request(request)))

    async def awrap_model_call(self, request, handler):
        """Async counterpart of :meth:`wrap_model_call`."""
        if not self._is_final_turn(request):
            return await handler(request)
        return self._drop_tool_calls(request, await handler(self._final_request(request)))


# ── Parsed-resume guardrail ───────────────────────────────────────────────────

class ResumeAnalysisError(RuntimeError):
    """The resume analyzer exhausted its guardrail retries without a valid result."""


class GuardrailState(AgentState):
    """Agent state extension: how many guardrail retries have been spent."""

    guardrail_retries: NotRequired[int]


class ParsedResumeGuardrailMiddleware(AgentMiddleware[GuardrailState]):
    """
    Validate the resume analyzer's result when the agent finishes.

    Three checks run in order; the first failure sends feedback back to the
    model (``jump_to="model"``):

      1. Save invariant — ``save_parsed_resume`` reported success. This is
         the load-bearing check: downstream nodes consume ``ctx.state["json"]``.
      2. URL format — every ``*_url`` field of the saved JSON is a valid
         http(s) URL or empty (a secondary net; the save tool already rejects
         invalid URLs).
      3. Faithfulness — the LLM judge scores the saved JSON against the
         extracted resume text. A score below the threshold clears the save so
         the agent must re-extract and save again. A judge *error* (e.g. the
         LLM is unavailable) fails open: a transient outage must not block a
         correctly-saved resume.

    After ``max_retries`` failed retries the middleware raises
    :class:`ResumeAnalysisError`, which aborts the whole pipeline before any
    downstream node runs.

    Args:
        ctx: The run's resume-analysis context (shared with the save tool).
        judge: Faithfulness judge; ``None`` (or an empty ``ctx.resume_text``)
            skips check 3.
        max_retries: Retries allowed after the first attempt.
    """

    state_schema = GuardrailState

    def __init__(
        self,
        ctx: "ResumeAnalysisContext",
        judge: "FaithfulnessJudge | None" = None,
        max_retries: int = 3,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.judge = judge
        self.max_retries = max_retries

    def check(self) -> str | None:
        """Run the checks; return feedback for the agent, or None when all pass."""
        ctx = self.ctx
        if not (ctx.state.get("saved") and ctx.state.get("json")):
            return (
                "The parsed resume was NOT saved. You MUST call the save_parsed_resume "
                "tool with the complete parsed resume JSON and get a SUCCESS response "
                "before producing your final answer. Fix any ERROR the tool reports "
                "and try again."
            )
        normalized = ctx.state["json"]

        try:
            invalid_urls = find_invalid_url_fields(
                ParsedResume.model_validate_json(normalized).result
            )
        except ValidationError:
            invalid_urls = []
        if invalid_urls:
            return (
                f"These URL fields are not valid URLs: {', '.join(invalid_urls)}. "
                "Every *_url field must be a complete http(s) URL or an empty "
                "string \"\". If only the link's label/anchor text is visible (not "
                "the actual URL), set that field to \"\". Fix these and call "
                "save_parsed_resume again."
            )

        if self.judge is None or not ctx.resume_text:
            return None
        try:
            verdict = self.judge.evaluate(reference=ctx.resume_text, output=normalized)
        except Exception as exc:  # noqa: BLE001 — fail open on judge errors
            logger.warning(
                "middleware: faithfulness judge errored for resume %d (passing the "
                "saved output through): %s", ctx.resume_id, exc,
            )
            return None
        if self.judge.passes(verdict):
            return None

        # Require a fresh save of the corrected document.
        ctx.state.pop("saved", None)
        unsupported = "".join(f"\n  - {claim}" for claim in verdict.unsupported_claims)
        return (
            f"The extracted data was not fully grounded in the resume (faithfulness "
            f"score {verdict.score}/10, minimum {self.judge.threshold}). "
            f"{verdict.feedback}"
            + (f"\nUnsupported values:{unsupported}" if unsupported else "")
            + "\nRe-extract using ONLY facts present in the resume, then call "
            "save_parsed_resume again with the corrected JSON."
        )

    @hook_config(can_jump_to=["model"])
    def after_agent(self, state: GuardrailState, runtime: "Runtime") -> dict[str, Any] | None:
        """Accept the result, or send the agent back with feedback."""
        feedback = self.check()
        if feedback is None:
            return None
        retries = state.get("guardrail_retries", 0)
        if retries >= self.max_retries:
            raise ResumeAnalysisError(
                f"Resume analysis failed its guardrail after {self.max_retries} "
                f"retries: {feedback}"
            )
        logger.warning(
            "middleware: guardrail rejected the resume analysis for resume %d "
            "(retry %d/%d): %s",
            self.ctx.resume_id, retries + 1, self.max_retries, feedback,
        )
        return {
            "messages": [HumanMessage(feedback)],
            "guardrail_retries": retries + 1,
            "jump_to": "model",
        }
