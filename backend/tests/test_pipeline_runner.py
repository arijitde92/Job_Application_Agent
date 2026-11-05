"""
Tests for the pipeline runner: progress steps driven by the graph's update
stream, trace-input redaction, and the result assembled from graph state.
The graph itself is faked here (see test_pipeline_graph.py for real runs).
"""

import pytest

from app.services import pipeline_runner
from app.services.extractors import text_job_extractor
from app.services.extractors.linkedin_extractor import JobDetails
from app.services.pipeline import graph as pipeline_graph

RESUME_MD = (
    b"# Ada Lovelace\n\nBackend engineer with Python, FastAPI and PostgreSQL experience.\n"
    b"Built analytical engines and telemetry platforms for robotics fleets.\n"
)


class FakeGraph:
    def __init__(self, updates):
        self.updates = updates
        self.calls = []

    def stream(self, state, config, stream_mode):
        self.calls.append({"state": state, "config": config, "stream_mode": stream_mode})
        yield from self.updates


@pytest.fixture
def steps(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        pipeline_runner, "_update_progress", lambda job_id, step, error=None: recorded.append(step)
    )
    return recorded


def test_progress_waits_for_both_parallel_nodes(steps):
    graph = FakeGraph([
        {"resume_analysis": {"parsed_resume_json": "{}"}},
        {"github_summary": {"github_summary": "{}"}},
        {"profile": {"applicant_profile": "P"}},
        {"resume_strategy": {"tailored_resume_md": "R"}},
        {"interview_preparation": {"interview_materials_md": "I"}},
    ])

    state = pipeline_runner._stream_pipeline(1, graph, {"inputs": {}}, {}, include_github=True)

    # building_profile only once BOTH branches are done, then tailoring.
    assert steps == ["building_profile", "tailoring_resume"]
    assert state["tailored_resume_md"] == "R"
    assert state["interview_materials_md"] == "I"
    assert graph.calls[0]["stream_mode"] == "updates"


def test_progress_without_github(steps):
    graph = FakeGraph([
        {"resume_analysis": {"parsed_resume_json": "{}"}},
        {"profile": {"applicant_profile": "P"}},
    ])
    pipeline_runner._stream_pipeline(1, graph, {"inputs": {}}, {}, include_github=False)
    assert steps == ["building_profile", "tailoring_resume"]


def test_trace_inputs_redact_resume_bytes():
    redacted = pipeline_runner._trace_inputs({"resume_bytes": b"%PDF-1.7 secret", "job_id": 3})
    assert redacted == {"resume_bytes": "<15 bytes>", "job_id": 3}


def test_run_pipeline_sync_returns_outputs_from_graph_state(monkeypatch, steps):
    monkeypatch.setattr(
        text_job_extractor, "extract_job_details_from_text",
        lambda text: JobDetails(url="", job_name="Engineer", company_name="Acme", job_description=text),
    )
    built = {}
    fake_graph = FakeGraph([
        {"resume_analysis": {"parsed_resume_json": "{}"}},
        {"profile": {"applicant_profile": "P"}},
        {"resume_strategy": {"tailored_resume_md": "# Tailored"}},
        {"interview_preparation": {"interview_materials_md": "# Interview"}},
    ])

    def fake_build_pipeline(**kwargs):
        built.update(kwargs)
        with open(kwargs["resume_path"], encoding="utf-8") as f:
            built["resume_text"] = f.read()
        return fake_graph

    monkeypatch.setattr(pipeline_graph, "build_pipeline", fake_build_pipeline)

    result = pipeline_runner._run_pipeline_sync(
        job_id=5, user_id=2, user_email="ada.lovelace@example.com",
        resume_bytes=RESUME_MD, resume_filename="resume.md", resume_id=9,
        job_description_text="Backend engineer role at Acme. " * 5,
    )

    assert result["tailored_resume"] == "# Tailored"
    assert result["interview_materials"] == "# Interview"
    assert result["tailored_resume_docx"] is None  # no docx tool success
    assert result["docx_filename"] == "ada_lovelace_Acme_Engineer_resume.docx"
    assert built["include_github"] is False
    assert built["docx_ctx"].job_id == 5
    assert built["resume_ctx"].resume_text == built["resume_text"]
    assert "Ada Lovelace" in built["resume_text"]

    config = fake_graph.calls[0]["config"]
    assert config["metadata"]["job_id"] == 5
    assert "web" in config["tags"]
    inputs = fake_graph.calls[0]["state"]["inputs"]
    assert inputs["company_name"] == "Acme"
    assert "github_url" not in inputs
    assert steps[:3] == ["extracting_job_info", "parsing_resume", "building_profile"]
    assert steps[-1] == "tailoring_resume"
