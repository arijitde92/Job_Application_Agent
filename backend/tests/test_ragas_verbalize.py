"""Unit tests for the RAGAS verbalizer and golden-dataset loading (no API key needed)."""

import pytest

from app.services.resume_parser import (
    EducationItem,
    ParsedResumeResult,
    PositionItem,
    ProjectItem,
)
from tests.ragas.common.dataset import GOLDEN_DATASET_ROOT, load_samples
from tests.ragas.common.verbalize import verbalize_parsed_resume


def test_empty_result_verbalizes_to_empty_string():
    assert verbalize_parsed_resume(ParsedResumeResult()) == ""


def test_empty_fields_produce_no_noise_claims():
    result = ParsedResumeResult(name="Jane Doe")
    text = verbalize_parsed_resume(result)
    assert text == "The applicant's name is Jane Doe."
    # No statements about empty urls, zero experience, false booleans, ...
    for noise in ("twitter", "LinkedIn", "years of professional experience",
                  "remote work", "management"):
        assert noise not in text


def test_populated_fields_are_all_verbalized():
    result = ParsedResumeResult(
        name="Jane Doe",
        email="jane@example.com",
        years_of_experience=2.4,
        has_remote_work_experience=True,
        remote_work_type="hybrid",
        linkedin_url="https://www.linkedin.com/in/janedoe",
        positions=[
            PositionItem(
                position_name="ML Engineer",
                company_name="Acme",
                start_date="2021-01-01",
                end_date=None,
                skills=["Python", "PyTorch"],
                job_details="Built training pipelines.",
            )
        ],
        education_qualifications=[
            EducationItem(school_name="MIT", degree_type="MSc",
                          faculty_department="Computer Science")
        ],
        projects=[ProjectItem(project_name="ResumeBot", description="A CV parser")],
        skills={"Languages": ["Python", "SQL"], "Default": []},
    )
    text = verbalize_parsed_resume(result)

    assert "The applicant's name is Jane Doe." in text
    assert "jane@example.com" in text
    assert "2.4 years of professional experience" in text
    assert "remote work experience (hybrid)" in text
    assert "https://www.linkedin.com/in/janedoe" in text
    assert "worked as ML Engineer at Acme from 2021-01-01 until the present" in text
    assert "Python, PyTorch" in text
    assert "Built training pipelines." in text
    assert "MSc in Computer Science at MIT" in text
    assert "ResumeBot" in text
    assert "'Languages' include: Python, SQL" in text
    # The empty "Default" skills category must not appear.
    assert "The applicant's skills include:" not in text


def test_verbalizer_is_deterministic():
    result = ParsedResumeResult(name="Jane Doe", skills={"Default": ["Python"]})
    assert verbalize_parsed_resume(result) == verbalize_parsed_resume(result)


def _dataset_versions() -> list[str]:
    root = GOLDEN_DATASET_ROOT / "resume_analyzer"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "manifest.json").exists())


@pytest.mark.parametrize("version", _dataset_versions() or ["<none>"])
def test_golden_dataset_loads_and_validates(version):
    if version == "<none>":
        pytest.skip("no golden dataset bootstrapped yet")
    samples = load_samples("resume_analyzer", version)
    assert samples, f"dataset {version} has no samples"
    for sample in samples:
        assert sample.context_text.strip(), f"{sample.sample_id}: empty context"
        # Ground truth already validated by load_samples; the verbalization
        # must be non-empty for a real resume.
        assert verbalize_parsed_resume(sample.ground_truth.result).strip(), (
            f"{sample.sample_id}: ground truth verbalizes to nothing"
        )
