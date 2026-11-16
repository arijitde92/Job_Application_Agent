"""
app.services.latex.layout
-------------------------
Deterministic layout rules for the tailored resume: which experience entries
are shown and in which order the sections appear. Applied by both document
routes — the LaTeX renderer (:func:`plan_layout`, on the validated
``TailoredResume``) and the docx fallback tool (:func:`plan_layout_for_dict`,
on the agent's raw JSON object).

Rules
~~~~~
* **Internships** are shown only when the candidate has no actual (non-intern)
  work experience. As soon as one real position exists, every internship is
  left out.
* **Early-career layout.** When the candidate's total work experience is
  ``None`` (no actual positions) or under one year, the sections are ordered::

      Professional Summary, Education, Skills, Work Experience (internships
      included when there is no actual experience), Projects, Certifications,
      Publications

  Otherwise the route's standard order applies (LaTeX:
  :data:`STANDARD_SECTION_ORDER`; docx: the document service's default).
  Empty sections are always omitted.

Total experience is computed here — never taken from the LLM — as the union
of the actual positions' date ranges in whole months (inclusive, so
"JUN 2024 – MAY 2025" is 12 months), with overlapping positions counted once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.services.latex.schemas import ExperienceEntry, TailoredResume, normalize_date

MONTHS_PER_YEAR = 12

STANDARD_SECTION_ORDER: Tuple[str, ...] = (
    "summary", "experience", "education", "skills",
    "projects", "publications", "certifications",
)
EARLY_CAREER_SECTION_ORDER: Tuple[str, ...] = (
    "summary", "education", "skills", "experience",
    "projects", "certifications", "publications",
)

# A title / work mode naming an internship ("Research Intern", "Summer
# Internship", "Interns"). Word-bounded so "Internal Auditor" or
# "International Sales" never match.
_INTERNSHIP_RE = re.compile(r"\bintern(?:s|ship|ships)?\b", re.IGNORECASE)

_MONTH_NUMBERS = {
    m: i for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    )
}


@dataclass(frozen=True)
class _Role:
    """The layout-relevant facts of one experience entry, from either route."""

    internship: bool
    # Normalised dates ("MON YYYY", "YYYY", "PRESENT" or ""); None = unparseable.
    start_date: Optional[str]
    end_date: Optional[str]


def _looks_like_internship(flag: bool, *texts: str) -> bool:
    return bool(flag or any(_INTERNSHIP_RE.search(t or "") for t in texts))


def _role_from_entry(entry: ExperienceEntry) -> _Role:
    return _Role(
        internship=_looks_like_internship(entry.is_internship, entry.job_title, entry.work_mode),
        start_date=entry.start_date,
        end_date=entry.end_date,
    )


def _lenient_date(value: Any) -> Optional[str]:
    try:
        return normalize_date("" if value is None else str(value))
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return value is True or value == 1


def _role_from_dict(entry: Any) -> _Role:
    """A role from the docx tool's raw JSON; bad values are treated as unknown."""
    if not isinstance(entry, Mapping):
        return _Role(internship=False, start_date="", end_date=None)
    return _Role(
        internship=_looks_like_internship(
            _truthy(entry.get("is_internship")),
            str(entry.get("job_title") or ""),
            str(entry.get("work_mode") or ""),
        ),
        start_date=_lenient_date(entry.get("start_date")),
        end_date=_lenient_date(entry.get("end_date")),
    )


def is_internship(entry: ExperienceEntry) -> bool:
    """True when the entry is flagged as, or titled as, an internship."""
    return _role_from_entry(entry).internship


def _month_index(value: Optional[str], *, is_end: bool, today: date) -> Optional[int]:
    """
    A normalised resume date as a month count (year * 12 + month0).

    "PRESENT" — and a blank end date, i.e. a current position — is ``today``.
    A year-only date covers the whole year: JAN as a start, DEC as an end.
    A blank start date, or an unparseable date (``None``), is unknown.
    """
    if value is None:
        return None
    if value == "PRESENT" or (is_end and not value):
        return today.year * MONTHS_PER_YEAR + today.month - 1
    if not value:
        return None
    parts = value.split(" ")
    if len(parts) == 2:
        return int(parts[1]) * MONTHS_PER_YEAR + _MONTH_NUMBERS[parts[0]]
    return int(parts[0]) * MONTHS_PER_YEAR + (MONTHS_PER_YEAR - 1 if is_end else 0)


def _experience_months(roles: Sequence[_Role], today: date) -> Optional[int]:
    actual = [r for r in roles if not r.internship]
    if not actual:
        return None

    spans: List[Tuple[int, int]] = []
    for r in actual:
        start = _month_index(r.start_date, is_end=False, today=today)
        end = _month_index(r.end_date, is_end=True, today=today)
        if start is not None and end is not None and end >= start:
            spans.append((start, end))

    total, current = 0, None
    for start, end in sorted(spans):
        if current is None or start > current[1] + 1:
            if current is not None:
                total += current[1] - current[0] + 1
            current = [start, end]
        else:
            current[1] = max(current[1], end)
    if current is not None:
        total += current[1] - current[0] + 1
    return total


def total_experience_months(
    entries: Iterable[ExperienceEntry], today: Optional[date] = None
) -> Optional[int]:
    """
    Total actual (non-internship) work experience in whole months.

    Returns:
        ``None`` when there is no actual position at all; otherwise the number
        of distinct months covered by the actual positions. Positions without
        a usable start date (or that end before they start) contribute 0.
    """
    return _experience_months([_role_from_entry(e) for e in entries], today or date.today())


@dataclass(frozen=True)
class ResumeLayout:
    """What a document renders: the section order and the shown positions."""

    early_career: bool
    # None = no actual work experience (internships do not count).
    experience_months: Optional[int]
    # The positions shown under Work Experience (internships only when there
    # is no actual experience) — ExperienceEntry models or raw dicts,
    # matching the input.
    experience: Tuple[Any, ...]
    # Non-empty sections in render order.
    sections: Tuple[str, ...]

    def describe(self) -> str:
        """A one-line summary for logs and the agent's tool result."""
        if self.experience_months is None:
            exp = "no full-time work experience"
        else:
            exp = f"{self.experience_months} month(s) of work experience"
        kind = "early-career" if self.early_career else "standard"
        return f"{kind} layout ({exp}); section order: {', '.join(self.sections)}"


def _plan(
    entries: Sequence[Any],
    roles: Sequence[_Role],
    has_section: Mapping[str, bool],
    today: Optional[date],
    standard_order: Sequence[str],
) -> ResumeLayout:
    months = _experience_months(roles, today or date.today())
    early_career = months is None or months < MONTHS_PER_YEAR
    if months is None:
        shown = tuple(entries)  # internships only — keep them
    else:
        shown = tuple(e for e, r in zip(entries, roles) if not r.internship)

    present = dict(has_section, experience=bool(shown))
    order = EARLY_CAREER_SECTION_ORDER if early_career else tuple(standard_order)
    return ResumeLayout(
        early_career=early_career,
        experience_months=months,
        experience=shown,
        sections=tuple(s for s in order if present.get(s)),
    )


def plan_layout(resume: TailoredResume, today: Optional[date] = None) -> ResumeLayout:
    """
    Decide the LaTeX resume's section order and the experience entries shown.

    Args:
        resume: Validated resume content.
        today: Reference date for "PRESENT" (defaults to today); pass a fixed
            date for reproducible output.
    """
    entries = list(resume.experience)
    return _plan(
        entries,
        [_role_from_entry(e) for e in entries],
        {
            "summary": bool(resume.summary),
            "education": bool(resume.education),
            "skills": bool(resume.skills),
            "projects": bool(resume.projects),
            "publications": bool(resume.publications),
            "certifications": bool(resume.certifications),
        },
        today,
        STANDARD_SECTION_ORDER,
    )


def plan_layout_for_dict(
    resume: Mapping[str, Any],
    standard_order: Sequence[str],
    today: Optional[date] = None,
) -> ResumeLayout:
    """
    The same rules applied to an unvalidated resume JSON object (docx route).

    Dates are normalised leniently — an unparseable date is treated as
    unknown rather than rejected, since the docx service validates the
    content itself. ``layout.experience`` holds the original entry objects.

    Args:
        resume: The agent's resume object.
        standard_order: The route's section order for experienced candidates.
        today: Reference date for "PRESENT" (defaults to today).
    """
    raw = resume.get("experience")
    entries = list(raw) if isinstance(raw, list) else []
    sections = set(standard_order) | set(EARLY_CAREER_SECTION_ORDER)
    return _plan(
        entries,
        [_role_from_dict(e) for e in entries],
        {s: bool(resume.get(s)) for s in sections if s != "experience"},
        today,
        standard_order,
    )
