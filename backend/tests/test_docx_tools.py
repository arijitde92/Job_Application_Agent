"""
Tests for the ``generate_resume_docx`` tool factory.

The tool is what the resume_strategist agent calls to render the tailored
resume as a .docx via the external document service, so these tests exercise
it the way the agent does — through ``.run()`` — with the HTTP call
monkeypatched out. They pin down the parts the agent cannot see: the
deterministic metadata envelope, the 3-attempt cap, the docx magic-byte check,
and that every failure comes back as an "ERROR: ..." string instead of an
exception.
"""

import json

import pytest
import requests

import app.services.crew.docx_tools as docx_tools
from app.services.crew.docx_tools import (
    ResumeDocxContext,
    _coerce_resume_object,
    build_docx_filename,
    make_generate_resume_docx_tool,
    sanitize_filename_component,
)

# A minimal-but-representative resume object, in the shape the task
# description instructs the agent to build.
SAMPLE_RESUME = {
    "personal_information": {"name": "Ada Lovelace", "email": "ada@example.com"},
    "summary": "Pioneering engineer.",
    "experience": [
        {
            "job_title": "Engineer",
            "company": "Analytical Engines Ltd",
            "start_date": "JAN 2020",
            "end_date": "PRESENT",
            "bullets": ["Built **everything**."],
        }
    ],
    "skills": [{"category": "Languages", "skills": ["Python"]}],
}

DOCX_BYTES = b"PK\x03\x04fake-docx-zip-content"


class FakeResponse:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


def make_ctx(tmp_path) -> ResumeDocxContext:
    return ResumeDocxContext(
        applicant_name="ada_lovelace",
        company_name="Acme Corp",
        job_name="Senior Engineer",
        job_id=42,
        output_path=str(tmp_path / "out_resume.docx"),
        display_name="Ada Lovelace",
    )


@pytest.fixture
def posted(monkeypatch):
    """Monkeypatch requests.post inside docx_tools; records calls, returns 200+PK."""
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(200, DOCX_BYTES)

    monkeypatch.setattr(docx_tools.requests, "post", fake_post)
    return calls


# ── Success path ──────────────────────────────────────────────────────────────

def test_success_writes_docx_sets_state_and_instructs_markdown(tmp_path, posted):
    ctx = make_ctx(tmp_path)
    tool = make_generate_resume_docx_tool(ctx)

    result = tool.run(resume_json=json.dumps(SAMPLE_RESUME))

    assert result.startswith("SUCCESS:")
    assert "Markdown" in result  # the agent must still emit the md final answer
    assert ctx.state["docx_path"] == ctx.output_path
    assert ctx.state["attempts"] == 1
    with open(ctx.output_path, "rb") as f:
        assert f.read() == DOCX_BYTES


def test_metadata_envelope_is_deterministic(tmp_path, posted):
    ctx = make_ctx(tmp_path)
    make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(SAMPLE_RESUME))

    assert len(posted) == 1
    payload = posted[0]["json"]
    assert payload["resume"] == SAMPLE_RESUME
    meta = payload["metadata"]
    assert meta["request_id"] == "job-42-attempt-1"
    assert meta["template"] == "modern"
    assert meta["page_size"] == "letter"
    assert meta["document_name"] == "Ada Lovelace Resume"
    # Only the sections present in the resume, in the default order, and
    # never personal_information (it is the document header).
    assert meta["section_order"] == ["summary", "experience", "skills"]


def test_section_order_includes_all_present_sections(tmp_path, posted):
    resume = dict(
        SAMPLE_RESUME,
        education=[{"degree": "PhD"}],
        publications=[{"citation": "A paper."}],
        projects=[{"name": "Engine", "bullets": ["b"]}],
        certifications=[{"name": "Cert", "issuer": "Org"}],
    )
    ctx = make_ctx(tmp_path)
    make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(resume))

    assert posted[0]["json"]["metadata"]["section_order"] == [
        "summary", "experience", "education", "skills",
        "publications", "projects", "certifications",
    ]


def test_fenced_json_string_is_accepted(tmp_path, posted):
    fenced = "```json\n" + json.dumps(SAMPLE_RESUME) + "\n```"
    ctx = make_ctx(tmp_path)
    result = make_generate_resume_docx_tool(ctx).run(resume_json=fenced)
    assert result.startswith("SUCCESS:")


# ── Input coercion ────────────────────────────────────────────────────────────

def test_coerce_accepts_dict_and_fenced_string():
    assert _coerce_resume_object(SAMPLE_RESUME) is SAMPLE_RESUME
    fenced = "```json\n" + json.dumps(SAMPLE_RESUME) + "\n```"
    assert _coerce_resume_object(fenced) == SAMPLE_RESUME


@pytest.mark.parametrize("bad", ["not json {", '["a", "list"]', 12345])
def test_coerce_rejects_non_objects(bad):
    with pytest.raises(ValueError):
        _coerce_resume_object(bad)


# ── Validation failures (no POST, but an attempt is consumed) ─────────────────

def test_missing_personal_information_errors_without_post(tmp_path, posted):
    resume = {k: v for k, v in SAMPLE_RESUME.items() if k != "personal_information"}
    ctx = make_ctx(tmp_path)
    result = make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(resume))

    assert result.startswith("ERROR:")
    assert "personal_information" in result
    assert posted == []
    assert ctx.state["attempts"] == 1


def test_no_content_sections_errors_without_post(tmp_path, posted):
    resume = {"personal_information": {"name": "Ada Lovelace"}}
    ctx = make_ctx(tmp_path)
    result = make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(resume))

    assert result.startswith("ERROR:")
    assert posted == []


# ── Service failures are retryable ERROR strings, never exceptions ───────────

def test_non_200_response_reports_status_and_body(tmp_path, monkeypatch):
    monkeypatch.setattr(
        docx_tools.requests, "post",
        lambda *a, **k: FakeResponse(422, b"", '{"detail": "bad section"}'),
    )
    ctx = make_ctx(tmp_path)
    result = make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(SAMPLE_RESUME))

    assert result.startswith("ERROR:")
    assert "422" in result
    assert "bad section" in result


def test_non_docx_body_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        docx_tools.requests, "post",
        lambda *a, **k: FakeResponse(200, b"<html>error page</html>"),
    )
    ctx = make_ctx(tmp_path)
    result = make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(SAMPLE_RESUME))

    assert result.startswith("ERROR:")
    assert ".docx" in result
    assert "docx_path" not in ctx.state


def test_connection_error_is_retryable(tmp_path, monkeypatch):
    def raise_conn_error(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(docx_tools.requests, "post", raise_conn_error)
    ctx = make_ctx(tmp_path)
    result = make_generate_resume_docx_tool(ctx).run(resume_json=json.dumps(SAMPLE_RESUME))

    assert result.startswith("ERROR:")
    assert "again" in result


# ── The 3-attempt cap ─────────────────────────────────────────────────────────

def test_attempt_cap_stops_after_three_calls(tmp_path, monkeypatch):
    calls = []

    def raise_conn_error(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("service down")

    monkeypatch.setattr(docx_tools.requests, "post", raise_conn_error)
    ctx = make_ctx(tmp_path)
    tool = make_generate_resume_docx_tool(ctx)

    for _ in range(3):
        result = tool.run(resume_json=json.dumps(SAMPLE_RESUME))
        assert result.startswith("ERROR:")

    # Fourth call: terminal error, no further POST, agent told to fall back.
    result = tool.run(resume_json=json.dumps(SAMPLE_RESUME))
    assert result.startswith("ERROR:")
    assert "Do NOT call generate_resume_docx again" in result
    assert "Markdown" in result
    assert len(calls) == 3
    assert ctx.state["attempts"] == 3


# ── Filename helpers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Corp", "Acme_Corp"),
        ("AI/ML Engineer (Sr.)", "AI_ML_Engineer_Sr"),
        ("  spaced   out  ", "spaced_out"),
        ("", "resume"),
        ("///", "resume"),
        ("Ünïcödé Name", "n_c_d_Name"),
    ],
)
def test_sanitize_filename_component(raw, expected):
    assert sanitize_filename_component(raw) == expected


def test_sanitize_truncates_long_components():
    assert len(sanitize_filename_component("x" * 500)) == 60


def test_build_docx_filename_format():
    assert (
        build_docx_filename("ada_lovelace", "Acme Corp", "Senior AI Engineer")
        == "ada_lovelace_Acme_Corp_Senior_AI_Engineer_resume.docx"
    )
