"""Tests for LangSmith configuration, run configs and the run-log callback."""

import os
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.core.config import Settings, export_langsmith_env
from app.services.pipeline.tracing import (
    PIPELINE_RUN_NAME,
    RECURSION_LIMIT,
    PipelineFileLogger,
    annotate_current_trace,
    build_run_config,
)


def test_settings_export_langsmith_env(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        _env_file=None, LANGSMITH_TRACING=True, LANGSMITH_API_KEY="ls-key",
        LANGSMITH_PROJECT="proj", LANGSMITH_ENDPOINT="",
    )

    export_langsmith_env(settings)

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "ls-key"
    assert os.environ["LANGSMITH_PROJECT"] == "proj"
    assert "LANGSMITH_ENDPOINT" not in os.environ  # blank values stay unset


def test_tracing_is_off_by_default(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert Settings(_env_file=None).LANGSMITH_TRACING is False


def test_run_config_carries_job_metadata_and_tags():
    handler = object()
    config = build_run_config(
        include_github=True, job_id=12, user_id=3, resume_id=9,
        callbacks=[handler], extra_tags=["web"],
    )
    assert config["run_name"] == PIPELINE_RUN_NAME
    assert config["metadata"] == {"include_github": True, "job_id": 12, "user_id": 3, "resume_id": 9}
    assert config["tags"] == ["job-application-agent", "with-github", "web"]
    assert config["callbacks"] == [handler]
    assert config["recursion_limit"] == RECURSION_LIMIT


def test_run_config_omits_unknown_ids():
    config = build_run_config(include_github=False)
    assert config["metadata"] == {"include_github": False}
    assert "no-github" in config["tags"]


def test_annotate_current_trace_is_a_noop_without_a_trace():
    annotate_current_trace(metadata={"job_id": 1}, tags=["x"])  # must not raise


def test_file_logger_records_nodes_tools_and_model_answers(tmp_path):
    path = tmp_path / "run.log"
    log = PipelineFileLogger(str(path), node_names=["profile"])
    run_id = uuid4()

    log.on_chain_start({}, {}, run_id=run_id, name="profile")
    log.on_chain_start({}, {}, run_id=run_id, name="SummarizationMiddleware.before_model")
    log.on_tool_start({"name": "read_resume"}, "{}", run_id=run_id)
    log.on_tool_end("resume text", run_id=run_id)
    log.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="final profile"))]]),
        run_id=run_id,
    )
    log.on_chain_end({}, run_id=run_id, name="profile")
    log.close()
    log.on_tool_end("after close", run_id=run_id)  # ignored, no error

    text = path.read_text(encoding="utf-8")
    assert "node start: profile" in text
    assert "Summarization" not in text
    assert "--> tool read_resume" in text
    assert "<-- tool result: resume text" in text
    assert "model answer: final profile" in text
    assert "node end: profile" in text
    assert "after close" not in text
