"""
Tests for the tailored-resume view/download endpoints: LaTeX jobs serve a PDF
compiled on demand (never stored), older/fallback jobs keep their stored files.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import jobs as jobs_api
from app.main import app
from app.services.latex.service import LatexCompileError


class _User:
    id = 1
    email = "applicant@example.com"
    first_name = "Test"
    last_name = "User"


def _job(**overrides):
    base = dict(
        id=5, user_id=1, job_name="AI Engineer", company_name="Acme Corp",
        tailored_resume_latex_gcs_path=None, tailored_resume_pdf_gcs_path=None,
        tailored_resume_gcs_path=None, tailored_resume_md_gcs_path=None,
        interview_materials_gcs_path=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def client_for():
    def make(job):
        class FakeDB:
            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: job)

        async def fake_db():
            yield FakeDB()

        app.dependency_overrides[deps.get_current_user] = lambda: _User()
        app.dependency_overrides[deps.get_db] = fake_db
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_latex_job_pdf_is_compiled_on_demand(client_for, monkeypatch):
    calls = []

    def fake_pdf(job_id, path):
        calls.append((job_id, path))
        return b"%PDF-1.7 compiled"

    monkeypatch.setattr(jobs_api, "get_resume_pdf_bytes", fake_pdf)
    client = client_for(_job(tailored_resume_latex_gcs_path="gs://b/t/5_r.tex"))

    view = client.get("/api/jobs/5/resume/pdf")
    download = client.get("/api/jobs/5/resume/download")

    assert view.status_code == download.status_code == 200
    assert view.content == download.content == b"%PDF-1.7 compiled"
    assert view.headers["content-type"] == "application/pdf"
    assert view.headers["content-disposition"].startswith("inline;")
    assert download.headers["content-disposition"].startswith(
        'attachment; filename="Acme_Corp_AI_Engineer_resume.pdf"'
    )
    assert calls == [(5, "gs://b/t/5_r.tex")] * 2


def test_compile_failure_is_a_502(client_for, monkeypatch):
    def boom(job_id, path):
        raise LatexCompileError("! LaTeX Error")

    monkeypatch.setattr(jobs_api, "get_resume_pdf_bytes", boom)
    client = client_for(_job(tailored_resume_latex_gcs_path="gs://b/t/5_r.tex"))
    resp = client.get("/api/jobs/5/resume/pdf")
    assert resp.status_code == 502
    assert "could not be generated" in resp.json()["detail"]


def test_legacy_job_downloads_its_stored_pdf(client_for, monkeypatch):
    monkeypatch.setattr(jobs_api, "download_file", lambda path: b"%PDF stored:" + path.encode())
    client = client_for(_job(
        tailored_resume_pdf_gcs_path="gs://b/t/5_r.pdf",
        tailored_resume_gcs_path="gs://b/t/5_r.docx",
    ))
    resp = client.get("/api/jobs/5/resume/download")
    assert resp.status_code == 200
    assert resp.content == b"%PDF stored:gs://b/t/5_r.pdf"
    assert resp.headers["content-type"] == "application/pdf"


def test_markdown_only_job_still_downloads_markdown(client_for, monkeypatch):
    monkeypatch.setattr(jobs_api, "download_file", lambda path: b"# Resume")
    client = client_for(_job(tailored_resume_gcs_path="gs://b/t/5_resume.md"))
    resp = client.get("/api/jobs/5/resume/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert client.get("/api/jobs/5/resume/pdf").status_code == 404


def test_delete_removes_the_latex_and_evicts_the_cache(client_for, monkeypatch):
    deleted, evicted = [], []
    monkeypatch.setattr(jobs_api, "delete_file", deleted.append)
    monkeypatch.setattr(jobs_api.pdf_cache, "evict", evicted.append)
    job = _job(tailored_resume_latex_gcs_path="gs://b/t/5_r.tex", tailored_resume_gcs_path="gs://b/t/5_r.md")

    class FakeDB:
        async def execute(self, stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: job)

        async def delete(self, obj):
            pass

        async def commit(self):
            pass

    async def fake_db():
        yield FakeDB()

    client = client_for(job)
    app.dependency_overrides[deps.get_db] = fake_db
    resp = client.delete("/api/jobs/5")
    assert resp.status_code == 204
    assert "gs://b/t/5_r.tex" in deleted
    assert evicted == [5]
