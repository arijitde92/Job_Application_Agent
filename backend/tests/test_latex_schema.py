"""Tests for the TailoredResume Pydantic schema (generate_resume_latex's args)."""

import pytest
from pydantic import ValidationError

from app.services.latex.schemas import TailoredResume, normalize_date, normalize_url
from tests.latex_fixtures import sample_resume


def test_sample_resume_is_valid():
    resume = TailoredResume.model_validate(sample_resume())
    assert resume.personal_information.name == "Ada Lovelace"
    assert resume.experience[0].end_date == "PRESENT"
    # Missing scheme is added.
    assert resume.personal_information.linkedin == "https://linkedin.com/in/ada"


@pytest.mark.parametrize("raw, expected", [
    ("SEP 2025", "SEP 2025"),
    ("September 2025", "SEP 2025"),
    ("sept. 2025", "SEP 2025"),
    ("Jan, 2020", "JAN 2020"),
    ("2019", "2019"),
    ("Present", "PRESENT"),
    ("current", "PRESENT"),
    ("", ""),
])
def test_dates_are_normalised(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", ["09/2025", "Sometime 2025", "SEP 25", "next year"])
def test_bad_dates_are_rejected(raw):
    with pytest.raises(ValueError, match="invalid date"):
        normalize_date(raw)


@pytest.mark.parametrize("raw, expected", [
    ("github.com/ada", "https://github.com/ada"),
    ("http://example.org/x", "http://example.org/x"),
    ("", ""),
])
def test_urls_are_normalised(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["not a url", "LinkedIn", "https://", "https://exa mple.com"])
def test_bad_urls_are_rejected(raw):
    with pytest.raises(ValueError, match="invalid URL"):
        normalize_url(raw)


def test_missing_name_is_rejected():
    data = sample_resume()
    data["personal_information"]["name"] = "  "
    with pytest.raises(ValidationError) as exc:
        TailoredResume.model_validate(data)
    assert exc.value.errors()[0]["loc"] == ("personal_information", "name")


def test_unbalanced_bold_is_rejected():
    data = sample_resume()
    data["experience"][0]["bullets"] = ["Built **LangGraph agents"]
    with pytest.raises(ValidationError, match="unbalanced"):
        TailoredResume.model_validate(data)


def test_bad_email_and_phone_are_rejected():
    data = sample_resume()
    data["personal_information"]["email"] = "ada-at-example"
    data["personal_information"]["phone"] = "call me"
    with pytest.raises(ValidationError) as exc:
        TailoredResume.model_validate(data)
    fields = {e["loc"][-1] for e in exc.value.errors()}
    assert fields == {"email", "phone"}


def test_resume_needs_summary_or_experience():
    data = {"personal_information": {"name": "Ada"}, "skills": [{"category": "X", "skills": ["y"]}]}
    with pytest.raises(ValidationError, match="needs content"):
        TailoredResume.model_validate(data)


def test_unknown_keys_are_ignored():
    data = sample_resume()
    data["metadata"] = {"template": "modern"}
    data["experience"][0]["team_size"] = 5
    TailoredResume.model_validate(data)  # does not raise


def test_validation_is_idempotent():
    once = TailoredResume.model_validate(sample_resume())
    twice = TailoredResume.model_validate(once.model_dump())
    assert once == twice
