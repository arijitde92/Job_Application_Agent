"""
Scripted LangChain chat models for offline tests.

``FakeToolCallingChatModel`` replays a list of responses — ``AIMessage``s
(optionally carrying ``tool_calls``) or callables that build one from the
incoming messages — so a real ``create_agent`` loop, middleware and tools can
be exercised without any network access. It records every call (messages,
bound tools, tool_choice) for assertions.

``FakeStructuredChatModel`` serves ``with_structured_output`` with scripted
pydantic objects (or exceptions), for the faithfulness judge.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import Field, PrivateAttr

Response = AIMessage | Callable[[list[BaseMessage]], AIMessage]


def tool_call(name: str, args: dict | None = None, call_id: str | None = None) -> dict:
    """Build a tool-call dict for an ``AIMessage``."""
    return {"name": name, "args": args or {}, "id": call_id or f"call_{name}", "type": "tool_call"}


def ai_calls(*calls: dict, content: str = "") -> AIMessage:
    """An ``AIMessage`` that requests the given tool calls."""
    return AIMessage(content=content, tool_calls=list(calls))


class FakeToolCallingChatModel(BaseChatModel):
    """Replay scripted responses; the last one repeats once the script runs out."""

    responses: list[Any] = Field(default_factory=list)
    model_name: str = "fake-model"
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    tool_choices: list[Any] = Field(default_factory=list)
    _index: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Runnable:
        self.bound_tool_names.append(
            [t.get("name", "?") if isinstance(t, dict) else t.name for t in tools]
        )
        self.tool_choices.append(tool_choice)
        return self

    def _next_response(self, messages: list[BaseMessage]) -> AIMessage:
        with self._lock:
            self.calls.append(list(messages))
            if not self.responses:
                return AIMessage(content="")
            response = self.responses[min(self._index, len(self.responses) - 1)]
            self._index += 1
        message = response(messages) if callable(response) else response
        # Fresh ids per call so repeated scripted tool calls stay distinct.
        if message.tool_calls:
            message = message.model_copy(update={
                "tool_calls": [
                    {**tc, "id": f"{tc['id']}_{self._index}"} for tc in message.tool_calls
                ]
            })
        return message

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next_response(messages))])


class FakeStructuredChatModel(BaseChatModel):
    """Serve ``with_structured_output`` with scripted objects or exceptions."""

    outputs: list[Any] = Field(default_factory=list)
    model_name: str = "fake-judge"
    calls: list[Any] = Field(default_factory=list)
    _index: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "fake-structured"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # pragma: no cover
        raise NotImplementedError("use with_structured_output")

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        def respond(messages: Any) -> Any:
            self.calls.append(messages)
            output = self.outputs[min(self._index, len(self.outputs) - 1)]
            self._index += 1
            if isinstance(output, Exception):
                raise output
            return output

        return RunnableLambda(respond)
