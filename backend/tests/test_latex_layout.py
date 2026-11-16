"""
Tests for the LaTeX resume layout rules (app.services.latex.layout):

* total work experience is None or < 1 year → early-career section order
  (Summary, Education, Skills, Work Experience, Projects, Certifications,
  Publications);
* internships are shown only when there is no actual work experience.
"""

from datetime import date

import pytest

from app.services.latex.layout import (
    EARLY_CAREER_SECTION_ORDER,
    STANDARD_SECTION_ORDER,
    is_internship,
    plan_layout,
    plan_layout_for_dict,
    total_experience_months,
)
from app.services.latex.renderer import render_resume_latex
from app.services.latex.schemas import ExperienceEntry, TailoredResume
from tests.latex_fixtures import sample_resume

TODAY = date(2026, 11, 16)

SECTION_HEADINGS = {
    "summary": r"\section{Professional Summary}",
    "experience": r"\section{Work Experience}",
    "education": r"\section{Education}",
    "skills": r"\section{Skills}",
    "projects": r"\section{Projects}",
    "publications": r"\section{Publications}",
    "certifications": r"\section{Certifications}",
}


def _job(title="Engineer", start="", end="", internship=False, work_mode=""):
    return {
        "job_title": title, "company": "Co", "work_mode": work_mode,
        "start_date": start, "end_date": end, "bullets": ["Did things."],
        "is_internship": internship,
    }


def _resume(experience):
    data = sample_resume()
    data["experience"] = experience
    return TailoredResume.model_validate(data)


def _entries(*jobs):
    return [ExperienceEntry.model_validate(j) for j in jobs]


def _section_order(tex):
    found = [(tex.index(h), name) for name, h in SECTION_HEADINGS.items() if h in tex]
    return tuple(name for _, name in sorted(found))


# ── internship detection ─────────────────────────────────────────────────────

@pytest.mark.parametrize("title,work_mode,flag,expected", [
    ("Software Engineer", "", True, True),
    ("Research Intern", "", False, True),
    ("Summer Internship — ML", "", False, True),
    ("ML Engineer", "Remote, Internship", False, True),
    ("Internal Auditor", "", False, False),
    ("International Sales Lead", "", False, False),
    ("Graduate Engineer Trainee", "Full time", False, False),
])
def test_is_internship(title, work_mode, flag, expected):
    entry = ExperienceEntry.model_validate(_job(title, internship=flag, work_mode=work_mode))
    assert is_internship(entry) is expected


# ── total experience ─────────────────────────────────────────────────────────

def test_no_actual_positions_is_none():
    assert total_experience_months([], TODAY) is None
    assert total_experience_months(_entries(_job("Intern", "JAN 2024", "DEC 2025")), TODAY) is None


def test_months_are_inclusive():
    assert total_experience_months(_entries(_job(start="JUN 2024", end="MAY 2025")), TODAY) == 12
    assert total_experience_months(_entries(_job(start="JUN 2024", end="JUN 2024")), TODAY) == 1


def test_present_and_blank_end_run_to_today():
    assert total_experience_months(_entries(_job(start="JUN 2026", end="PRESENT")), TODAY) == 6
    assert total_experience_months(_entries(_job(start="JUN 2026", end="")), TODAY) == 6


def test_year_only_dates_cover_the_whole_year():
    assert total_experience_months(_entries(_job(start="2023", end="2023")), TODAY) == 12


def test_overlapping_positions_count_once_and_internships_are_ignored():
    jobs = _entries(
        _job(start="JAN 2025", end="JUN 2025"),
        _job(start="APR 2025", end="AUG 2025"),           # overlaps → JAN–AUG = 8
        _job(start="SEP 2025", end="OCT 2025"),           # adjacent → +2 = 10
        _job("Intern", start="JAN 2020", end="DEC 2024"),  # ignored
    )
    assert total_experience_months(jobs, TODAY) == 10


def test_undated_or_inverted_positions_contribute_nothing():
    jobs = _entries(_job(start="", end="2024"), _job(start="MAR 2025", end="JAN 2025"))
    assert total_experience_months(jobs, TODAY) == 0


# ── layout ───────────────────────────────────────────────────────────────────

def test_experienced_candidate_uses_standard_order_and_hides_internships():
    resume = _resume([
        _job("Senior Engineer", "JAN 2020", "PRESENT"),
        _job("Research Intern", "JUN 2019", "DEC 2019", internship=True),
    ])
    layout = plan_layout(resume, TODAY)
    assert not layout.early_career
    assert layout.sections == STANDARD_SECTION_ORDER
    assert [e.job_title for e in layout.experience] == ["Senior Engineer"]


def test_exactly_one_year_is_not_early_career():
    layout = plan_layout(_resume([_job(start="DEC 2025", end="PRESENT")]), TODAY)
    assert layout.experience_months == 12
    assert not layout.early_career


def test_under_one_year_uses_early_career_order_and_still_hides_internships():
    resume = _resume([
        _job("Engineer", "MAR 2026", "PRESENT"),
        _job("Intern", "JAN 2025", "DEC 2025", internship=True),
    ])
    layout = plan_layout(resume, TODAY)
    assert layout.early_career and layout.experience_months == 9
    assert layout.sections == EARLY_CAREER_SECTION_ORDER
    assert [e.job_title for e in layout.experience] == ["Engineer"]


def test_only_internships_uses_early_career_order_and_shows_them():
    resume = _resume([
        _job("ML Intern", "JAN 2025", "DEC 2025", internship=True),
        _job("Research Internship", "JUN 2024", "AUG 2024"),
    ])
    layout = plan_layout(resume, TODAY)
    assert layout.early_career and layout.experience_months is None
    assert layout.sections == EARLY_CAREER_SECTION_ORDER
    assert len(layout.experience) == 2


def test_no_experience_at_all_omits_the_section():
    layout = plan_layout(_resume([]), TODAY)
    assert layout.early_career
    assert layout.sections == tuple(s for s in EARLY_CAREER_SECTION_ORDER if s != "experience")


def test_describe_mentions_the_layout():
    assert plan_layout(_resume([]), TODAY).describe().startswith(
        "early-career layout (no full-time work experience)"
    )


@pytest.mark.parametrize("experience", [
    [_job("Senior Engineer", "JAN 2020", "PRESENT"), _job("Intern", "JUN 2019", "DEC 2019", True)],
    [_job("Engineer", "MAR 2026", "PRESENT"), _job("Intern", "JAN 2025", "DEC 2025", True)],
    [_job("ML Intern", "JAN 2025", "DEC 2025", True), _job("Research Internship", "JUN 2024", "AUG 2024")],
    [],
])
def test_dict_layout_matches_model_layout(experience):
    """The docx route (raw dict) and the LaTeX route (model) agree."""
    data = sample_resume()
    data["experience"] = experience
    from_model = plan_layout(TailoredResume.model_validate(data), TODAY)
    from_dict = plan_layout_for_dict(data, STANDARD_SECTION_ORDER, TODAY)
    assert from_dict.sections == from_model.sections
    assert from_dict.experience_months == from_model.experience_months
    assert [e["job_title"] for e in from_dict.experience] == [
        e.job_title for e in from_model.experience
    ]


def test_dict_layout_tolerates_malformed_entries():
    layout = plan_layout_for_dict(
        {"summary": "s", "experience": ["not an object", {"job_title": None}]},
        STANDARD_SECTION_ORDER, TODAY,
    )
    assert layout.early_career and layout.experience_months == 0
    assert layout.sections == ("summary", "experience")


# ── rendered LaTeX ───────────────────────────────────────────────────────────

def test_rendered_early_career_section_order():
    resume = _resume([_job("Data Intern", "JAN 2025", "JUN 2025", internship=True)])
    tex = render_resume_latex(resume, today=TODAY)
    assert _section_order(tex) == (
        "summary", "education", "skills", "experience",
        "projects", "certifications", "publications",
    )
    assert "Data Intern" in tex


def test_rendered_standard_order_drops_internships():
    resume = _resume([
        _job("Staff Engineer", "JAN 2018", "PRESENT"),
        _job("Summer Intern", "JUN 2017", "AUG 2017", internship=True),
    ])
    tex = render_resume_latex(resume, today=TODAY)
    assert _section_order(tex) == STANDARD_SECTION_ORDER
    assert "Staff Engineer" in tex and "Summer Intern" not in tex


def test_certifications_are_spaced_when_not_last():
    tex = render_resume_latex(_resume([]), today=TODAY)
    certs = tex.index(r"\section{Certifications}")
    pubs = tex.index(r"\section{Publications}")
    assert r"\end{itemize}" + "\n" + r"\vspace{-8pt}" in tex[certs:pubs]
