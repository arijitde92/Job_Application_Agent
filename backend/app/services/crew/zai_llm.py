"""
zai_llm.py
----------
CrewAI LLM binding for Z.ai-hosted GLM models (GLM-5.2 and siblings).

CrewAI 1.x has no native Z.ai/GLM provider. Its ``LLM(...)`` factory only
routes to a fixed provider list (openai, anthropic, azure, gemini, bedrock,
snowflake, plus the OpenAI-compatible set: openrouter, deepseek, ollama,
hosted_vllm, cerebras, dashscope) — anything else falls through to LiteLLM,
which this project does not install. So neither ``LLM(model="zai/glm-5.2")``
nor ``LLM(model="glm-5.2", provider="zai")`` can work.

Z.ai does expose a fully OpenAI-compatible endpoint
(https://api.z.ai/api/paas/v4), so the working route is to reuse CrewAI's
*native* OpenAI completion class and point it at Z.ai. That gives us the
normal CrewAI code path — chat/completions, OpenAI-style function calling
(verified against GLM-5.2, including the ``strict: true`` schemas CrewAI
emits), stop words, streaming and usage events — with no LiteLLM dependency.

The thin subclass below exists because two things go wrong if you instead
write ``LLM(model="glm-5.2", provider="openai", base_url=...)`` by hand:

  - Credential fallback. ``OpenAICompletion`` falls back to ``OPENAI_API_KEY``
    / ``OPENAI_BASE_URL``. If ``ZAI_API_KEY`` is ever missing, requests would
    silently go to OpenAI with an OpenAI key instead of failing loudly.
  - Context window. ``OpenAICompletion.get_context_window_size()`` matches the
    model name against a table of OpenAI models and returns **8192** for
    anything unrecognised (~6.5k after CrewAI's usage ratio). With
    ``Agent(respect_context_window=True)`` that would make CrewAI start
    summarising/trimming the conversation almost immediately, even though
    GLM-5.2 accepts a 1M-token context.

Note: GLM-5.2 reasons by default and its reasoning tokens are billed against
``max_tokens``, so keep ``max_tokens`` generous. Reasoning text comes back in
a separate ``reasoning_content`` field which CrewAI ignores; ``content`` is
the answer. Thinking can be turned off per-LLM with
``additional_params={"thinking": {"type": "disabled"}}`` and its depth tuned
with ``additional_params={"reasoning_effort": "high"}`` — both are forwarded
verbatim into the request body by ``_prepare_completion_params``.
"""

from __future__ import annotations

import os
from typing import Any

from crewai.llms.providers.openai.completion import OpenAICompletion
from pydantic import model_validator

# Z.ai's OpenAI-compatible base URL. Overridable via ZAI_BASE_URL (e.g. to hit
# the mainland-China endpoint at https://open.bigmodel.cn/api/paas/v4).
ZAI_DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"

# Input context windows per Z.ai's model docs (https://docs.z.ai/guides/llm/).
# Used only to size CrewAI's context-window guardrail.
ZAI_CONTEXT_WINDOWS: dict[str, int] = {
    "glm-5.2": 1_000_000,
    "glm-5.1": 200_000,
    "glm-5-turbo": 200_000,
    "glm-5": 200_000,
    "glm-4.7": 200_000,
    "glm-4.6": 200_000,
    "glm-4.5-air": 128_000,
    "glm-4.5": 128_000,
}
ZAI_FALLBACK_CONTEXT_WINDOW = 128_000


class ZaiLLM(OpenAICompletion):
    """A CrewAI LLM that talks to Z.ai's OpenAI-compatible GLM endpoint.

    Usage:
        llm = ZaiLLM(model="glm-5.2", temperature=0.5, max_tokens=16000)

    The API key is taken from the ``api_key`` argument, else ``ZAI_API_KEY``;
    the base URL from ``base_url``, else ``ZAI_BASE_URL``, else Z.ai's default.
    """

    @model_validator(mode="before")
    @classmethod
    def _apply_zai_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        api_key = data.get("api_key") or os.environ.get("ZAI_API_KEY")
        if not api_key:
            raise ValueError(
                "ZAI_API_KEY is not set — required to call Z.ai GLM models. "
                "Add it to backend/.env or pass api_key=... explicitly."
            )
        data["api_key"] = api_key
        data["base_url"] = (
            data.get("base_url")
            or os.environ.get("ZAI_BASE_URL")
            or ZAI_DEFAULT_BASE_URL
        )
        return data

    def get_context_window_size(self) -> int:
        """Return GLM's real context window instead of OpenAI's 8k fallback."""
        from crewai.llm import CONTEXT_WINDOW_USAGE_RATIO

        window = ZAI_CONTEXT_WINDOWS.get(self.model, ZAI_FALLBACK_CONTEXT_WINDOW)
        return int(window * CONTEXT_WINDOW_USAGE_RATIO)
