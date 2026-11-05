"""
Tests for the chat-model factories, the fixed-window rate limiter and the
model registry. No network: request payloads are built locally.
"""

from types import SimpleNamespace

import pytest

from app.services.pipeline import llms
from app.services.pipeline.llms import (
    AgentRole,
    FixedWindowRateLimiter,
    ModelRegistry,
    build_claude_llm,
    build_gemini_llm,
    build_glm_llm,
    model_name,
    summarization_trigger_tokens,
)

KEY_NAMES = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ZAI_API_KEY", "ZAI_BASE_URL")


@pytest.fixture
def secrets(monkeypatch):
    """Point the factories at a controllable settings object (and a clean env)."""
    values = {name: "" for name in KEY_NAMES}
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llms, "get_settings", lambda: SimpleNamespace(**values))
    return values


# ── Factories ─────────────────────────────────────────────────────────────────

def test_glm_requires_an_explicit_zai_key(secrets, monkeypatch):
    # Even with an OpenAI key around, GLM must never fall back to it.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    with pytest.raises(ValueError, match="ZAI_API_KEY"):
        build_glm_llm()


def test_glm_targets_zai_chat_completions(secrets):
    secrets["ZAI_API_KEY"] = "zai-key"
    llm = build_glm_llm()

    assert llm.model_name == "glm-5.2"
    assert llm.openai_api_base == llms.ZAI_DEFAULT_BASE_URL
    assert llm.openai_api_key.get_secret_value() == "zai-key"
    assert llm.use_responses_api is False
    assert llm.temperature == 0.3
    # Sent as Z.ai's max_tokens, not OpenAI's max_completion_tokens.
    payload = llm._get_request_payload([("user", "hi")])
    assert payload["extra_body"] == {"max_tokens": 16000}
    assert "max_completion_tokens" not in payload


def test_glm_base_url_override(secrets):
    secrets.update(ZAI_API_KEY="k", ZAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4")
    assert build_glm_llm().openai_api_base == "https://open.bigmodel.cn/api/paas/v4"


def test_claude_sends_no_sampling_parameters(secrets):
    secrets["ANTHROPIC_API_KEY"] = "ant-key"
    llm = build_claude_llm()

    payload = llm._get_request_payload([("user", "hi")])
    assert payload["model"] == "claude-sonnet-5"
    assert payload["max_tokens"] == 16000
    for param in ("temperature", "top_p", "top_k", "thinking"):
        assert param not in payload


def test_gemini_settings(secrets):
    secrets["GEMINI_API_KEY"] = "g-key"
    llm = build_gemini_llm()
    assert llm.model == "gemini-3.5-flash-lite"
    assert llm.temperature == 0.5
    assert llm.max_output_tokens == 8000


def test_factories_attach_the_given_rate_limiter(secrets):
    secrets.update(GEMINI_API_KEY="g", ANTHROPIC_API_KEY="a", ZAI_API_KEY="z")
    limiter = FixedWindowRateLimiter()
    for factory in (build_gemini_llm, build_claude_llm, build_glm_llm):
        assert factory(rate_limiter=limiter).rate_limiter is limiter


def test_summarization_trigger_uses_the_model_window(secrets):
    secrets["ANTHROPIC_API_KEY"] = "a"
    claude = build_claude_llm()
    assert model_name(claude) == "claude-sonnet-5"
    assert summarization_trigger_tokens(claude) == int(200_000 * 0.85)


# ── FixedWindowRateLimiter ────────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_allows_a_burst_then_waits_for_the_window():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(10, 60.0, clock=clock, sleep=clock.sleep)

    for _ in range(10):
        assert limiter.acquire() is True
    assert clock.sleeps == []  # the whole burst went straight through

    assert limiter.acquire(blocking=False) is False
    assert limiter.acquire() is True
    assert clock.sleeps == [60.0]


def test_rate_limiter_resets_after_the_window():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(2, 10.0, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    limiter.acquire()
    clock.now = 10.0
    assert limiter.acquire(blocking=False) is True


async def test_rate_limiter_async_acquire():
    limiter = FixedWindowRateLimiter(1, 60.0)
    assert await limiter.aacquire() is True
    assert await limiter.aacquire(blocking=False) is False


@pytest.mark.parametrize("kwargs", [{"max_requests": 0}, {"window_seconds": 0}])
def test_rate_limiter_validates_arguments(kwargs):
    with pytest.raises(ValueError):
        FixedWindowRateLimiter(**kwargs)


# ── ModelRegistry ─────────────────────────────────────────────────────────────

def test_registry_builds_lazily_and_caches_per_role():
    built = []

    def factory():
        built.append(1)
        return object()

    registry = ModelRegistry({AgentRole.PROFILER: factory, AgentRole.RESUME_ANALYZER: factory})
    assert built == []
    first = registry.get(AgentRole.PROFILER)
    assert registry.get(AgentRole.PROFILER) is first
    assert len(built) == 1


def test_registry_unknown_role_raises():
    with pytest.raises(KeyError):
        ModelRegistry({}).get(AgentRole.PROFILER)


def test_default_factories_cover_every_role():
    assert set(llms.DEFAULT_FACTORIES) == set(AgentRole)


def test_each_role_gets_its_own_rate_limiter(secrets):
    secrets["GEMINI_API_KEY"] = "g"
    registry = ModelRegistry()
    profiler = registry.get(AgentRole.PROFILER)
    strategist = registry.get(AgentRole.RESUME_STRATEGIST)
    assert profiler is not strategist
    assert isinstance(profiler.rate_limiter, FixedWindowRateLimiter)
    assert profiler.rate_limiter is not strategist.rate_limiter
