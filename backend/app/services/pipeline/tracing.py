"""
app.services.pipeline.tracing
-----------------------------
LangSmith tracing and run configuration for the pipeline.

Tracing is controlled entirely by environment variables, which
``app.core.config.get_settings()`` exports from ``backend/.env``:

    LANGSMITH_TRACING=true          # on/off for every run (web app and CLI)
    LANGSMITH_API_KEY=...
    LANGSMITH_PROJECT=job-application-agent
    LANGSMITH_ENDPOINT=             # optional (EU / self-hosted)

When it is on, each job produces ONE trace: the runner's ``@traceable`` root
span (job extraction, resume parsing), the LangGraph run with its parallel
GitHub / resume-analysis branches, each agent as a named subgraph, every model
and tool call, and the faithfulness judge. :func:`build_run_config` stamps the
job / user / resume ids as trace metadata so traces can be filtered per job.

Traces contain resume text and other personal data — enable tracing only for
a LangSmith workspace you are allowed to store applicant data in.

Independently of LangSmith, :class:`PipelineFileLogger` writes a readable
step-by-step log of every run to ``backend/logs/``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig
from langsmith.run_helpers import get_current_run_tree

from app.core.logging import get_logger

logger = get_logger(__name__)

PIPELINE_RUN_NAME = "job_application_pipeline"
PIPELINE_TAG = "job-application-agent"

# LangGraph counts every node execution (model, tools and middleware hooks) as
# a step. The top-level graph has five nodes; each agent subgraph runs up to
# ~10 model calls with their tool and middleware steps, plus guardrail
# retries. The default of 25 would cut a normal agent run short.
RECURSION_LIMIT = 200

# Longest preview of a model answer / tool payload written to the run log.
_PREVIEW_CHARS = 600


def build_run_config(
    *,
    include_github: bool,
    job_id: int | None = None,
    user_id: int | None = None,
    resume_id: int | None = None,
    callbacks: Sequence[BaseCallbackHandler] = (),
    extra_tags: Iterable[str] = (),
    extra_metadata: Mapping[str, Any] | None = None,
) -> RunnableConfig:
    """
    Build the ``RunnableConfig`` for one pipeline invocation.

    Args:
        include_github: Whether the run includes the GitHub summarizer.
        job_id: Web-app job id (None for the CLI / tests).
        user_id: Owning user id.
        resume_id: Resume being tailored.
        callbacks: Extra callback handlers (usage tracking, file logging).
        extra_tags: Additional LangSmith tags.
        extra_metadata: Additional LangSmith metadata.

    Returns:
        A config with run name, tags, metadata, callbacks and recursion limit.
    """
    metadata: dict[str, Any] = {"include_github": include_github}
    for key, value in (("job_id", job_id), ("user_id", user_id), ("resume_id", resume_id)):
        if value is not None:
            metadata[key] = value
    metadata.update(extra_metadata or {})
    tags = [PIPELINE_TAG, "with-github" if include_github else "no-github", *extra_tags]
    return RunnableConfig(
        run_name=PIPELINE_RUN_NAME,
        tags=tags,
        metadata=metadata,
        callbacks=list(callbacks),
        recursion_limit=RECURSION_LIMIT,
    )


def annotate_current_trace(
    metadata: Mapping[str, Any] | None = None, tags: Iterable[str] = ()
) -> None:
    """
    Add metadata/tags to the active LangSmith span (no-op when not tracing).

    Used by the runner to label its ``@traceable`` root span with the job's
    ids once they are known.
    """
    run_tree = get_current_run_tree()
    if run_tree is None:
        return
    if metadata:
        run_tree.add_metadata(dict(metadata))
    tags = list(tags)
    if tags:
        run_tree.add_tags(tags)


def _preview(value: Any) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + " …"


class PipelineFileLogger(BaseCallbackHandler):
    """
    Callback handler that writes a readable execution log to a file.

    Records pipeline-node starts/ends, every tool call with its (truncated)
    input and output, every model answer (tool calls requested or a preview
    of the text), and errors. Thread-safe: the parallel graph branches share
    one handler.

    Args:
        path: Log file to append to.
        node_names: Graph node names to log start/end for.
    """

    def __init__(self, path: str, node_names: Iterable[str] = ()) -> None:
        self.path = path
        self.node_names = frozenset(node_names)
        self._lock = threading.Lock()
        self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115 — closed by close()

    def _write(self, line: str) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.write(line + "\n")
                self._file.flush()

    def close(self) -> None:
        """Close the log file."""
        with self._lock:
            self._file.close()

    def on_chain_start(self, serialized, inputs, *, run_id: UUID, name: str | None = None, **kwargs: Any) -> None:
        name = name or kwargs.get("metadata", {}).get("langgraph_node")
        if name in self.node_names:
            self._write(f"\n=== node start: {name} ===")

    def on_chain_end(self, outputs, *, run_id: UUID, name: str | None = None, **kwargs: Any) -> None:
        if name in self.node_names:
            self._write(f"=== node end: {name} ===")

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        name = kwargs.get("name")
        if name in self.node_names:
            self._write(f"!!! node error: {name}: {error!r}")

    def on_tool_start(self, serialized, input_str: str, *, run_id: UUID, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name", "tool")
        self._write(f"--> tool {name}({_preview(input_str)})")

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        content = getattr(output, "content", output)
        self._write(f"<-- tool result: {_preview(content)}")

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._write(f"!!! tool error: {error!r}")

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                tool_calls = getattr(message, "tool_calls", None) or []
                if tool_calls:
                    names = ", ".join(tc["name"] for tc in tool_calls)
                    self._write(f"... model requested tool(s): {names}")
                else:
                    text = message.text if message is not None else generation.text
                    self._write(f"... model answer: {_preview(text)}")

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._write(f"!!! model error: {error!r}")
