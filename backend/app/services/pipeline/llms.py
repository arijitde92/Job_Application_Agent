"""
app.services.pipeline.llms
--------------------------
Chat-model factories for the pipeline's agents.

Every agent runs on the model best suited to its job:

  - GitHub Project Summarizer -> GLM-5.2 (Z.ai), via ``langchain-openai``
    pointed at Z.ai's OpenAI-compatible endpoint.
  - Resume Analyzer           -> Claude Sonnet 5, via ``langchain-anthropic``.
    The faithfulness judge on the resume-analysis guardrail uses it too.
  - Profiler / Resume Strategist / Interview Preparer
                              -> Gemini 3.5 Flash Lite, via
                                 ``langchain-google-genai``.

Each agent role gets its OWN chat-model instance, with its own
``FixedWindowRateLimiter``, so one agent's burst of tool-calling turns never
throttles another agent. Instances are built lazily and cached by
``ModelRegistry`` — a run without a GitHub profile never builds (or needs an
API key for) the GLM model. Chat-model clients are thread-safe, so the cached
instances are shared by concurrent pipeline runs; the rate limiters therefore
cap each role's request rate process-wide.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Callable, Mapping

from langchain_core.rate_limiters import BaseRateLimiter

from app.core.config import get_settings

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic
    from langchain_core.language_models import BaseChatModel
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

# ── Model identifiers ─────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-3.5-flash-lite"
CLAUDE_MODEL = "claude-sonnet-5"
GLM_MODEL = "glm-5.2"

# Z.ai's OpenAI-compatible base URL. Overridable via ZAI_BASE_URL (e.g. the
# mainland-China endpoint at https://open.bigmodel.cn/api/paas/v4).
ZAI_DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"

# Input context windows, used only to size the conversation-summarization
# trigger (SummarizationMiddleware). GLM figures per https://docs.z.ai/guides/llm/.
CONTEXT_WINDOWS: dict[str, int] = {
    GEMINI_MODEL: 1_000_000,
    CLAUDE_MODEL: 200_000,
    "glm-5.2": 1_000_000,
    "glm-5.1": 200_000,
    "glm-5-turbo": 200_000,
    "glm-5": 200_000,
    "glm-4.7": 200_000,
    "glm-4.6": 200_000,
    "glm-4.5-air": 128_000,
    "glm-4.5": 128_000,
}
FALLBACK_CONTEXT_WINDOW = 128_000

# Summarize the conversation once it fills this fraction of the window,
# leaving headroom for the next response.
CONTEXT_WINDOW_USAGE_RATIO = 0.85

# Requests per minute allowed per agent role.
DEFAULT_REQUESTS_PER_MINUTE = 10


class AgentRole(StrEnum):
    """The model slots of the pipeline — one chat-model instance per role."""

    GITHUB_SUMMARIZER = "github_project_summarizer"
    RESUME_ANALYZER = "resume_analyzer"
    PROFILER = "profiler"
    RESUME_STRATEGIST = "resume_strategist"
    INTERVIEW_PREPARER = "interview_preparer"
    FAITHFULNESS_JUDGE = "faithfulness_judge"


# ── Rate limiting ─────────────────────────────────────────────────────────────

class FixedWindowRateLimiter(BaseRateLimiter):
    """
    Allow up to ``max_requests`` calls per ``window_seconds`` window.

    Calls inside the budget go through immediately (bursts are allowed);
    the first call over budget blocks until the current window ends, then a
    fresh window starts. This is deliberately NOT LangChain's
    ``InMemoryRateLimiter``: that is a token bucket that starts empty and
    refills at ``max_requests / window`` per second, so a 10-per-minute budget
    would space EVERY call six seconds apart — a large slowdown for an agent
    whose tool-calling turns take a second or two each.

    Args:
        max_requests: Calls allowed per window.
        window_seconds: Window length in seconds.
        clock: Monotonic time source (injectable for tests).
        sleep: Blocking sleep function (injectable for tests).
    """

    def __init__(
        self,
        max_requests: int = DEFAULT_REQUESTS_PER_MINUTE,
        window_seconds: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._window_start = clock()
        self._count = 0

    def _try_acquire(self) -> float:
        """Take one slot if available; return 0.0, else the seconds to wait."""
        with self._lock:
            now = self._clock()
            if now - self._window_start >= self.window_seconds:
                self._window_start = now
                self._count = 0
            if self._count < self.max_requests:
                self._count += 1
                return 0.0
            return self._window_start + self.window_seconds - now

    def acquire(self, *, blocking: bool = True) -> bool:
        """Acquire one request slot, blocking until one frees up if asked to."""
        while True:
            wait = self._try_acquire()
            if wait <= 0:
                return True
            if not blocking:
                return False
            self._sleep(wait)

    async def aacquire(self, *, blocking: bool = True) -> bool:
        """Async counterpart of :meth:`acquire`."""
        while True:
            wait = self._try_acquire()
            if wait <= 0:
                return True
            if not blocking:
                return False
            await asyncio.sleep(wait)


# ── Helpers ───────────────────────────────────────────────────────────────────

def model_name(llm: "BaseChatModel") -> str:
    """Return the provider model id of a LangChain chat model."""
    return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown")


def context_window(name: str) -> int:
    """Input context window (tokens) for a model id."""
    return CONTEXT_WINDOWS.get(name, FALLBACK_CONTEXT_WINDOW)


def summarization_trigger_tokens(llm: "BaseChatModel") -> int:
    """Token count at which an agent's conversation gets summarized."""
    return int(context_window(model_name(llm)) * CONTEXT_WINDOW_USAGE_RATIO)


def _secret(name: str) -> str:
    """Read an API key from settings (backend/.env), falling back to os.environ."""
    return getattr(get_settings(), name, "") or os.environ.get(name, "")


# ── Factories ─────────────────────────────────────────────────────────────────

def build_gemini_llm(
    *,
    temperature: float = 0.5,
    max_output_tokens: int = 8000,
    rate_limiter: BaseRateLimiter | None = None,
) -> "ChatGoogleGenerativeAI":
    """
    Gemini 3.5 Flash Lite, used for synthesis (profile, resume, interview prep).

    Args:
        temperature: Sampling temperature.
        max_output_tokens: Response cap.
        rate_limiter: Optional per-role limiter.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        api_key=_secret("GEMINI_API_KEY"),
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        rate_limiter=rate_limiter,
    )


def build_glm_llm(
    *,
    temperature: float = 0.3,
    max_tokens: int = 16000,
    rate_limiter: BaseRateLimiter | None = None,
) -> "ChatOpenAI":
    """
    GLM-5.2 on Z.ai's OpenAI-compatible endpoint, used for repository analysis.

    Three things here are load-bearing:

      - The API key is required explicitly. ``ChatOpenAI`` would otherwise fall
        back to ``OPENAI_API_KEY`` and silently send the request — and the
        applicant's data — to OpenAI.
      - ``use_responses_api=False``: Z.ai only implements Chat Completions.
      - The response cap is sent as ``max_tokens`` through ``extra_body``.
        ``ChatOpenAI`` serializes its own ``max_tokens`` field as the newer
        ``max_completion_tokens``, which Z.ai does not document. GLM-5.2 also
        reasons by default and bills reasoning tokens against this cap, so it
        stays generous; reasoning text arrives in ``reasoning_content``, which
        is ignored, and ``content`` is the answer.

    Raises:
        ValueError: If ZAI_API_KEY is not configured.
    """
    from langchain_openai import ChatOpenAI

    api_key = _secret("ZAI_API_KEY")
    if not api_key:
        raise ValueError(
            "ZAI_API_KEY is not set — required to call Z.ai GLM models. "
            "Add it to backend/.env."
        )
    return ChatOpenAI(
        model=GLM_MODEL,
        api_key=api_key,
        base_url=_secret("ZAI_BASE_URL") or ZAI_DEFAULT_BASE_URL,
        temperature=temperature,
        use_responses_api=False,
        extra_body={"max_tokens": max_tokens},
        rate_limiter=rate_limiter,
    )


def build_claude_llm(
    *,
    max_tokens: int = 16000,
    rate_limiter: BaseRateLimiter | None = None,
) -> "ChatAnthropic":
    """
    Claude Sonnet 5, used for structured resume extraction and as the
    faithfulness judge.

    No temperature / top_p / top_k: Sonnet 5 rejects non-default sampling
    parameters with a 400, and ``ChatAnthropic`` only sends them when set.
    No thinking config either: Sonnet 5 runs adaptive thinking by default, and
    the fixed-budget ``{"type": "enabled", "budget_tokens": N}`` form returns
    a 400. ``max_tokens`` caps thinking + response together; the parsed-resume
    JSON alone can exceed 8k tokens.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=CLAUDE_MODEL,
        api_key=_secret("ANTHROPIC_API_KEY"),
        max_tokens=max_tokens,
        rate_limiter=rate_limiter,
    )


def _with_limiter(factory: Callable[..., "BaseChatModel"]) -> Callable[[], "BaseChatModel"]:
    """Wrap a factory so each built model gets its own fixed-window limiter."""
    return lambda: factory(rate_limiter=FixedWindowRateLimiter())


DEFAULT_FACTORIES: Mapping[AgentRole, Callable[[], "BaseChatModel"]] = {
    AgentRole.GITHUB_SUMMARIZER: _with_limiter(build_glm_llm),
    AgentRole.RESUME_ANALYZER: _with_limiter(build_claude_llm),
    AgentRole.PROFILER: _with_limiter(build_gemini_llm),
    AgentRole.RESUME_STRATEGIST: _with_limiter(build_gemini_llm),
    AgentRole.INTERVIEW_PREPARER: _with_limiter(build_gemini_llm),
    AgentRole.FAITHFULNESS_JUDGE: _with_limiter(build_claude_llm),
}


class ModelRegistry:
    """
    Lazily builds and caches one chat model per :class:`AgentRole`.

    Pass explicit ``factories`` (or :meth:`from_models`) to swap in other
    models — the tests use this to inject fake chat models.
    """

    def __init__(
        self, factories: Mapping[AgentRole, Callable[[], "BaseChatModel"]] | None = None
    ) -> None:
        self._factories = dict(DEFAULT_FACTORIES if factories is None else factories)
        self._models: dict[AgentRole, "BaseChatModel"] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_models(cls, models: Mapping[AgentRole, "BaseChatModel"]) -> "ModelRegistry":
        """Build a registry that serves the given pre-built models."""
        return cls({role: (lambda m=model: m) for role, model in models.items()})

    def get(self, role: AgentRole) -> "BaseChatModel":
        """Return the cached model for ``role``, building it on first use.

        Raises:
            KeyError: If the registry has no factory for ``role``.
        """
        with self._lock:
            if role not in self._models:
                self._models[role] = self._factories[role]()
            return self._models[role]


@lru_cache(maxsize=1)
def default_registry() -> ModelRegistry:
    """The process-wide registry of production models."""
    return ModelRegistry()
