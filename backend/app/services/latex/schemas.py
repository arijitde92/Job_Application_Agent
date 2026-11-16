"""
app.services.latex.schemas
--------------------------
Pydantic schema of the tailored resume content that the resume strategist
agent passes to the ``generate_resume_latex`` tool.

The schema is the tool's ``args_schema``: the LLM fills these fields as
structured tool arguments and every value is validated (and normalised where
the fix is unambiguous) before the deterministic LaTeX renderer sees it.
Validation messages are phrased so the agent can correct its input and retry.

The field names mirror the JSON the docx fallback tool accepts, so the agent
can reuse the same content for either tool.
"""

from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Shared normalisers ────────────────────────────────────────────────────────

_MONTHS = {
    "JANUARY": "JAN", "FEBRUARY": "FEB", "MARCH": "MAR", "APRIL": "APR",
    "MAY": "MAY", "JUNE": "JUN", "JULY": "JUL", "AUGUST": "AUG",
    "SEPTEMBER": "SEP", "SEPT": "SEP", "OCTOBER": "OCT", "NOVEMBER": "NOV",
    "DECEMBER": "DEC",
}
_SHORT_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_PRESENT_WORDS = {"PRESENT", "CURRENT", "NOW", "TODAY", "ONGOING", "TILL DATE", "TO DATE"}
_DATE_RE = re.compile(r"^(?:(?:%s) )?\d{4}$" % "|".join(_SHORT_MONTHS))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[+\d][\d\s().\-]{5,24}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9\-]+)+(?::\d+)?$")

MAX_BULLET_CHARS = 450


def normalize_date(value: str) -> str:
    """
    Normalise a resume date to ``MON YYYY``, ``YYYY``, ``PRESENT`` or "".

    Full month names are shortened ("September 2025" → "SEP 2025") and
    "Present"/"Current" become ``PRESENT``.

    Raises:
        ValueError: The value is none of the accepted forms.
    """
    text = re.sub(r"[\s,]+", " ", (value or "").strip()).upper()
    if not text:
        return ""
    if text in _PRESENT_WORDS:
        return "PRESENT"
    parts = text.split(" ")
    if len(parts) == 2 and parts[0].rstrip(".") in _MONTHS:
        text = f"{_MONTHS[parts[0].rstrip('.')]} {parts[1]}"
    elif len(parts) == 2:
        text = f"{parts[0].rstrip('.')} {parts[1]}"
    if not _DATE_RE.match(text):
        raise ValueError(
            f"invalid date {value!r}: use a short uppercase month and year like "
            '"SEP 2025", a year like "2025", "PRESENT", or "" when unknown'
        )
    return text


def normalize_url(value: str) -> str:
    """
    Normalise a URL to an absolute http(s) URL, or "" when empty.

    A missing scheme is added ("github.com/ada" → "https://github.com/ada").

    Raises:
        ValueError: The value does not look like a web URL.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text.lstrip("/")
    match = re.match(r"^https?://([^/?#]+)", text, re.IGNORECASE)
    if " " in text or not match or not _HOST_RE.match(match.group(1)):
        raise ValueError(
            f"invalid URL {value!r}: give a complete web address such as "
            '"https://github.com/user", or "" if the resume shows no real URL'
        )
    return text


def _check_bullet(text: str) -> str:
    if not text:
        raise ValueError("bullet points must not be empty")
    if len(text) > MAX_BULLET_CHARS:
        raise ValueError(
            f"bullet point is {len(text)} characters long; keep each bullet under "
            f"{MAX_BULLET_CHARS} characters"
        )
    if text.count("**") % 2:
        raise ValueError(
            f"unbalanced ** bold markers in bullet {text[:60]!r}…: every **opening "
            "marker needs a closing **"
        )
    return text


class _Model(BaseModel):
    """Base config: trim strings, tolerate (ignore) unknown keys from the LLM."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


# ── Section models ────────────────────────────────────────────────────────────

class PersonalInformation(_Model):
    """The resume header."""

    name: str = Field(min_length=1, max_length=100, description="The applicant's full name.")
    location: str = Field(default="", max_length=100, description='City and country, e.g. "Kolkata, India".')
    email: str = Field(default="", max_length=254, description="Email address, or \"\".")
    phone: str = Field(default="", max_length=30, description='Phone number, e.g. "+91 8981800329", or "".')
    linkedin: str = Field(default="", max_length=300, description="LinkedIn profile URL, or \"\".")
    github: str = Field(default="", max_length=300, description="GitHub profile URL, or \"\".")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if v and not _EMAIL_RE.match(v):
            raise ValueError(f"invalid email address {v!r}")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if v and not _PHONE_RE.match(v):
            raise ValueError(
                f"invalid phone number {v!r}: use digits with an optional leading + "
                "and spaces, dashes, dots or parentheses"
            )
        return v

    @field_validator("linkedin", "github")
    @classmethod
    def _url(cls, v: str) -> str:
        return normalize_url(v)


class ExperienceEntry(_Model):
    """One position under Work Experience."""

    job_title: str = Field(min_length=1, max_length=120, description="Job title.")
    company: str = Field(min_length=1, max_length=120, description="Employer name.")
    location: str = Field(default="", max_length=100, description='Employer location, e.g. "Singapore".')
    work_mode: str = Field(default="", max_length=60, description='E.g. "Remote, Full time" or "Onsite".')
    start_date: str = Field(default="", description='Short uppercase month-year, e.g. "SEP 2025".')
    end_date: str = Field(default="", description='Short uppercase month-year, or "PRESENT".')
    bullets: List[str] = Field(
        default_factory=list, max_length=8,
        description="Tailored achievement bullets; **bold** markers allowed.",
    )
    is_internship: bool = Field(
        default=False,
        description=(
            "true when this position is an internship. Internships are shown only "
            "when the applicant has no other work experience."
        ),
    )

    @field_validator("start_date", "end_date")
    @classmethod
    def _dates(cls, v: str) -> str:
        return normalize_date(v)

    @field_validator("bullets")
    @classmethod
    def _bullets(cls, v: List[str]) -> List[str]:
        return [_check_bullet(b.strip()) for b in v]


class EducationEntry(_Model):
    """One degree under Education."""

    degree: str = Field(min_length=1, max_length=150, description='E.g. "M.E. Computer Science & Engineering".')
    institution: str = Field(min_length=1, max_length=150, description="University or school name.")
    location: str = Field(default="", max_length=100, description='E.g. "India".')
    gpa: str = Field(default="", max_length=20, description='GPA/grade as written on the resume, or "".')
    start_date: str = Field(default="", description='Short uppercase month-year, e.g. "AUG 2016".')
    end_date: str = Field(default="", description='Short uppercase month-year, or "PRESENT".')

    @field_validator("start_date", "end_date")
    @classmethod
    def _dates(cls, v: str) -> str:
        return normalize_date(v)


class SkillCategory(_Model):
    """One category line under Skills."""

    category: str = Field(min_length=1, max_length=60, description='E.g. "Languages".')
    skills: List[str] = Field(min_length=1, max_length=40, description="Skills in this category.")

    @field_validator("skills")
    @classmethod
    def _skills(cls, v: List[str]) -> List[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("a skill category needs at least one non-empty skill")
        return cleaned


class ProjectEntry(_Model):
    """One project under Projects."""

    name: str = Field(min_length=1, max_length=120, description="Project name.")
    url: str = Field(default="", max_length=300, description="Project URL, or \"\".")
    bullets: List[str] = Field(
        default_factory=list, max_length=6,
        description="Tailored project bullets; **bold** markers allowed.",
    )

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return normalize_url(v)

    @field_validator("bullets")
    @classmethod
    def _bullets(cls, v: List[str]) -> List[str]:
        return [_check_bullet(b.strip()) for b in v]


class PublicationEntry(_Model):
    """One publication citation."""

    citation: str = Field(min_length=1, max_length=600, description="The full citation text.")


class CertificationEntry(_Model):
    """One certification."""

    name: str = Field(min_length=1, max_length=200, description="Certification name.")
    issuer: str = Field(default="", max_length=120, description="Issuing organisation.")
    year: str = Field(default="", max_length=20, description='Year, e.g. "2026", or "".')
    url: str = Field(default="", max_length=500, description="Verification URL, or \"\".")

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return normalize_url(v)


# ── Top-level ─────────────────────────────────────────────────────────────────

class TailoredResume(_Model):
    """
    The complete tailored resume content rendered into the LaTeX template.

    Omit (or leave empty) any section the applicant has no content for.
    """

    personal_information: PersonalInformation = Field(
        description="Header details, copied exactly from the parsed resume."
    )
    summary: str = Field(default="", max_length=1500, description="The tailored professional summary.")
    experience: List[ExperienceEntry] = Field(default_factory=list, max_length=10)
    education: List[EducationEntry] = Field(default_factory=list, max_length=6)
    skills: List[SkillCategory] = Field(default_factory=list, max_length=10)
    projects: List[ProjectEntry] = Field(default_factory=list, max_length=6)
    publications: List[PublicationEntry] = Field(default_factory=list, max_length=15)
    certifications: List[CertificationEntry] = Field(default_factory=list, max_length=15)

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        if v.count("**") % 2:
            raise ValueError("unbalanced ** bold markers in summary")
        return v

    @model_validator(mode="after")
    def _has_content(self) -> "TailoredResume":
        if not (self.summary or self.experience):
            raise ValueError(
                'the resume needs content: include at least "summary" and '
                '"experience" (plus "education" and "skills" where available)'
            )
        return self
