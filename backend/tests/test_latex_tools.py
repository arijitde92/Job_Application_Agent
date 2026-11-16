"""
Tests for the resume strategist's LaTeX tools (generate_resume_latex,
compile_resume_pdf), the Tectonic compiler wrapper, and the on-demand PDF
service. GCS, the DB and the compiler are faked except in the real-compile
test, which runs only when Tectonic is installed.
"""

import pytest

import app.services.gcs_service as gcs_service
from app.services.latex import compiler as latex_compiler
from app.services.latex import service as latex_service
from app.services.latex.compiler import CompileResult, compile_latex_to_pdf
from app.services.latex.renderer import render_resume_latex
from app.services.latex.schemas import TailoredResume
from app.services.pipeline.tools import latex_tools
from app.services.pipeline.tools.latex_tools import (
    ResumeLatexContext,
    make_compile_resume_pdf_tool,
    make_generate_resume_latex_tool,
)
from tests.latex_fixtures import sample_resume


@pytest.fixture
def ctx(tmp_path):
    return ResumeLatexContext(
        user_id=2, job_id=7, applicant_name="ada", company_name="Acme Corp",
        job_name="AI Engineer", workdir=str(tmp_path),
    )


@pytest.fixture
def storage(monkeypatch):
    """Fake GCS upload + DB update; records what was written."""
    calls = {"uploads": [], "db": []}

    def fake_upload(user_id, job_id, content, filename):
        calls["uploads"].append((user_id, job_id, filename, content))
        return f"gs://bucket/tailored/{user_id}/{job_id}_{filename}"

    monkeypatch.setattr(gcs_service, "upload_tailored_resume", fake_upload)
    monkeypatch.setattr(latex_tools, "_record_latex_path", lambda c, p: calls["db"].append((c.job_id, p)))
    return calls


# ── generate_resume_latex ─────────────────────────────────────────────────────

def test_generate_renders_uploads_and_records(ctx, storage):
    result = make_generate_resume_latex_tool(ctx).invoke(sample_resume())

    assert result.startswith("SUCCESS")
    assert "compile_resume_pdf" in result
    assert "standard layout" in result  # the agent is told which layout was applied
    assert ctx.state["layout"].startswith("standard layout")
    expected_tex = render_resume_latex(TailoredResume.model_validate(sample_resume()))
    assert ctx.state["tex_source"] == expected_tex
    with open(ctx.state["tex_path"], encoding="utf-8") as f:
        assert f.read() == expected_tex
    (user_id, job_id, filename, content), = storage["uploads"]
    assert (user_id, job_id, filename) == (2, 7, "ada_Acme_Corp_AI_Engineer_resume.tex")
    assert content == expected_tex
    assert storage["db"] == [(7, "gs://bucket/tailored/2/7_ada_Acme_Corp_AI_Engineer_resume.tex")]
    assert ctx.state["gcs_path"] == storage["db"][0][1]


def test_generate_reports_validation_errors_to_the_agent(ctx, storage):
    data = sample_resume()
    data["experience"][0]["start_date"] = "sometime"
    data["personal_information"]["github"] = "not a url"

    result = make_generate_resume_latex_tool(ctx).invoke(data)

    assert result.startswith("ERROR")
    assert "experience.0.start_date" in result
    assert "personal_information.github" in result
    assert "2 attempt(s) left" in result
    assert storage["uploads"] == [] and storage["db"] == []
    assert not ctx.state.get("latex_ok")


def test_generate_attempt_cap(ctx, storage):
    tool = make_generate_resume_latex_tool(ctx)
    bad = {"personal_information": {"name": ""}}
    for _ in range(3):
        assert tool.invoke(bad).startswith("ERROR")
    final = tool.invoke(sample_resume())
    assert final.startswith("ERROR")
    assert "generate_resume_docx" in final
    assert storage["uploads"] == []


def test_generate_upload_failure_is_an_error(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bucket offline")

    monkeypatch.setattr(gcs_service, "upload_tailored_resume", boom)
    result = make_generate_resume_latex_tool(ctx).invoke(sample_resume())
    assert result.startswith("ERROR") and "bucket offline" in result
    assert not ctx.state.get("latex_ok")


def test_generate_local_only_skips_storage(ctx, storage):
    ctx.local_only = True
    assert make_generate_resume_latex_tool(ctx).invoke(sample_resume()).startswith("SUCCESS")
    assert storage["uploads"] == [] and storage["db"] == []


# ── compile_resume_pdf ────────────────────────────────────────────────────────

def test_compile_requires_generated_latex(ctx):
    result = make_compile_resume_pdf_tool(ctx).invoke({})
    assert result.startswith("ERROR") and "generate_resume_latex" in result


def test_compile_success_records_the_pdf(ctx, storage, monkeypatch, tmp_path):
    pdf = tmp_path / "resume.pdf"
    seen = {}

    def fake_compile(tex, workdir):
        seen["tex"] = tex
        pdf.write_bytes(b"%PDF-1.7 fake")
        return CompileResult(ok=True, pdf_path=str(pdf))

    monkeypatch.setattr(latex_tools, "compile_latex_to_pdf", fake_compile)
    make_generate_resume_latex_tool(ctx).invoke(sample_resume())
    tool = make_compile_resume_pdf_tool(ctx)

    result = tool.invoke({})

    assert result.startswith("SUCCESS")
    assert ctx.state["pdf_path"] == str(pdf)
    assert seen["tex"] == ctx.state["tex_source"]
    assert tool.invoke({}).startswith("SUCCESS: The PDF resume has already")


def test_compile_failure_returns_the_log_and_caps(ctx, storage, monkeypatch):
    monkeypatch.setattr(
        latex_tools, "compile_latex_to_pdf",
        lambda tex, workdir: CompileResult(ok=False, error="! Undefined control sequence."),
    )
    make_generate_resume_latex_tool(ctx).invoke(sample_resume())
    tool = make_compile_resume_pdf_tool(ctx)

    first = tool.invoke({})
    assert first.startswith("ERROR") and "Undefined control sequence" in first
    assert "2 compile attempt(s) left" in first
    tool.invoke({})
    third = tool.invoke({})
    assert "generate_resume_docx" in third
    assert tool.invoke({}).startswith("ERROR: compile_resume_pdf failed 3 times")
    assert "pdf_path" not in ctx.state


def test_regenerating_invalidates_the_compiled_pdf(ctx, storage):
    tool = make_generate_resume_latex_tool(ctx)
    tool.invoke(sample_resume())
    ctx.state["pdf_path"] = "/tmp/old.pdf"
    tool.invoke(sample_resume())
    assert "pdf_path" not in ctx.state


# ── compiler ──────────────────────────────────────────────────────────────────

def test_compiler_reports_a_missing_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(latex_compiler, "_resolve_binary", lambda: None)
    result = compile_latex_to_pdf("\\documentclass{article}", str(tmp_path))
    assert not result.ok and "not installed" in result.error


def test_error_excerpt_picks_tex_errors():
    log = "note: running\n! Undefined control sequence.\nl.42 \\foo\nmore\nnoise\n"
    excerpt = latex_compiler._error_excerpt(log)
    assert excerpt.startswith("! Undefined control sequence.")
    assert "l.42 \\foo" in excerpt
    assert "noise" not in excerpt


@pytest.mark.skipif(
    latex_compiler._resolve_binary() is None,
    reason="Tectonic not found (TECTONIC_PATH / PATH)",
)
def test_real_compile_produces_a_pdf(tmp_path):
    tex = render_resume_latex(TailoredResume.model_validate(sample_resume()))
    result = compile_latex_to_pdf(tex, str(tmp_path))
    assert result.ok, result.error
    with open(result.pdf_path, "rb") as f:
        assert f.read(4) == b"%PDF"


# ── on-demand service ─────────────────────────────────────────────────────────

def test_service_compiles_once_then_serves_the_cache(monkeypatch, tmp_path):
    tex = "\\documentclass{article}"
    monkeypatch.setattr(gcs_service, "download_file", lambda path: tex.encode())
    monkeypatch.setattr(latex_service.pdf_cache, "cache_dir", lambda: tmp_path)
    compiles = []

    def fake_compile(source, workdir):
        compiles.append(source)
        out = f"{workdir}/resume.pdf"
        with open(out, "wb") as f:
            f.write(b"%PDF-1.7 on demand")
        return CompileResult(ok=True, pdf_path=out)

    monkeypatch.setattr(latex_service, "compile_latex_to_pdf", fake_compile)

    assert latex_service.get_resume_pdf_bytes(9, "gs://b/x.tex") == b"%PDF-1.7 on demand"
    assert latex_service.get_resume_pdf_bytes(9, "gs://b/x.tex") == b"%PDF-1.7 on demand"
    assert compiles == [tex]  # second call served from the temp cache


def test_service_raises_on_compile_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(gcs_service, "download_file", lambda path: b"bad")
    monkeypatch.setattr(latex_service.pdf_cache, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        latex_service, "compile_latex_to_pdf",
        lambda s, w: CompileResult(ok=False, error="! LaTeX Error"),
    )
    with pytest.raises(latex_service.LatexCompileError, match="LaTeX Error"):
        latex_service.get_resume_pdf_bytes(9, "gs://b/x.tex")
