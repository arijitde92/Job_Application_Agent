"""
app.services.resume_parser
--------------------------
Document-type detection and text extraction for user-uploaded resumes
(.pdf / .docx / .md), plus the Pydantic models and JSON schema template
used by the resume_analyzer agent.

Extraction strategy:
    .pdf  — pypdf first; if it errors or yields near-empty text (e.g. a
            scanned PDF), fall back to pdfplumber; if that also fails,
            raise ResumeParsingError.
    .docx — python-docx (paragraphs + table cells); failures raise
            ResumeParsingError.
    .md   — file content used as-is.
"""

from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

# A pypdf result with fewer non-whitespace characters than this is treated as
# a failed extraction (typically a scanned/image-only PDF) and retried with
# pdfplumber.
_MIN_EXTRACTED_CHARS = 50

SUPPORTED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".md": "md"}


class ResumeParsingError(Exception):
    """Raised when a resume file cannot be parsed into text."""


# ── Document type detection ───────────────────────────────────────────────────

def detect_document_type(file_path: str) -> str:
    """
    Return the document type ("pdf" | "docx" | "md") for ``file_path``.

    Raises:
        ResumeParsingError: If the extension is not one of .pdf/.docx/.md.
    """
    suffix = Path(file_path).suffix.lower()
    doc_type = SUPPORTED_EXTENSIONS.get(suffix)
    if doc_type is None:
        raise ResumeParsingError(
            f"Unsupported resume document type '{suffix}' for file '{file_path}'. "
            f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    return doc_type


# ── Per-type extractors ───────────────────────────────────────────────────────

def _non_whitespace_len(text: str) -> int:
    return len("".join(text.split()))


def extract_pdf_text(file_path: str) -> str:
    """
    Extract text from a PDF resume.

    Tries pypdf first; on any exception or a near-empty result falls back to
    pdfplumber. If both fail, raises ResumeParsingError.
    """
    pypdf_error: Exception | None = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if _non_whitespace_len(text) >= _MIN_EXTRACTED_CHARS:
            logger.info(
                "resume_parser: pypdf extracted %d chars from '%s' (%d pages)",
                len(text), file_path, len(reader.pages),
            )
            return text
        logger.warning(
            "resume_parser: pypdf returned near-empty text (%d non-ws chars) for '%s'; "
            "falling back to pdfplumber",
            _non_whitespace_len(text), file_path,
        )
    except Exception as e:  # noqa: BLE001 — any pypdf failure triggers the fallback
        pypdf_error = e
        logger.warning(
            "resume_parser: pypdf failed for '%s' (%s); falling back to pdfplumber",
            file_path, e,
        )

    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        if _non_whitespace_len(text) < _MIN_EXTRACTED_CHARS:
            raise ResumeParsingError(
                f"pdfplumber extracted no meaningful text from '{file_path}' "
                "(possibly a scanned/image-only PDF)."
            )
        logger.info(
            "resume_parser: pdfplumber extracted %d chars from '%s'", len(text), file_path
        )
        return text
    except ResumeParsingError:
        logger.error(
            "resume_parser: PDF extraction failed for '%s' — pypdf%s and pdfplumber "
            "both produced no usable text",
            file_path, f" ({pypdf_error})" if pypdf_error else "",
            exc_info=True,
        )
        raise
    except Exception as e:
        logger.error(
            "resume_parser: PDF extraction failed for '%s' — pypdf error: %s; "
            "pdfplumber error: %s",
            file_path, pypdf_error, e, exc_info=True,
        )
        raise ResumeParsingError(
            f"Failed to parse PDF '{file_path}': pypdf error: {pypdf_error}; "
            f"pdfplumber error: {e}"
        ) from e


def extract_docx_text(file_path: str) -> str:
    """
    Extract text from a DOCX resume (paragraphs and table cells).

    Raises:
        ResumeParsingError: If python-docx cannot read the file or the file
            contains no meaningful text.
    """
    try:
        from docx import Document

        document = Document(file_path)
        parts = [para.text for para in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        text = "\n".join(part for part in parts if part and part.strip())
    except Exception as e:
        logger.error(
            "resume_parser: python-docx failed for '%s': %s", file_path, e, exc_info=True
        )
        raise ResumeParsingError(f"Failed to parse DOCX '{file_path}': {e}") from e

    if _non_whitespace_len(text) < _MIN_EXTRACTED_CHARS:
        logger.error(
            "resume_parser: DOCX '%s' contains no meaningful text (%d non-ws chars)",
            file_path, _non_whitespace_len(text),
        )
        raise ResumeParsingError(
            f"DOCX '{file_path}' contains no meaningful text."
        )
    logger.info(
        "resume_parser: python-docx extracted %d chars from '%s'", len(text), file_path
    )
    return text


def extract_md_text(file_path: str) -> str:
    """Return a Markdown resume's content as-is."""
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning(
            "resume_parser: '%s' is not valid utf-8; re-reading with errors='replace'",
            file_path,
        )
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(
            "resume_parser: failed to read markdown '%s': %s", file_path, e, exc_info=True
        )
        raise ResumeParsingError(f"Failed to read Markdown '{file_path}': {e}") from e
    logger.info("resume_parser: read %d chars from markdown '%s'", len(text), file_path)
    return text


def extract_resume_text(file_path: str) -> str:
    """
    Detect the document type of ``file_path`` and extract its text.

    Raises:
        ResumeParsingError: If the type is unsupported or extraction fails.
    """
    doc_type = detect_document_type(file_path)
    logger.info("resume_parser: extracting '%s' as %s", file_path, doc_type)
    if doc_type == "pdf":
        return extract_pdf_text(file_path)
    if doc_type == "docx":
        return extract_docx_text(file_path)
    return extract_md_text(file_path)


# ── Parsed-resume filename helpers ────────────────────────────────────────────

def sanitize_user_name(name: str) -> str:
    """Lowercase ``name`` and collapse every non-alphanumeric run to a single '_'."""
    sanitized = "".join(c if c.isalnum() else "_" for c in name.lower())
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    return sanitized.strip("_") or "user"


def build_parsed_resume_filename(user_name: str, user_id: int, resume_id: int) -> str:
    """Return '<user_name>_<user_id>_<resume_id>_parsed_resume.json'."""
    return f"{sanitize_user_name(user_name)}_{user_id}_{resume_id}_parsed_resume.json"


# ── Structured output models (guardrail validation) ──────────────────────────

class ProjectItem(BaseModel):
    project_name: str = ""
    description: str = ""
    url: str = ""


class PublicationItem(BaseModel):
    title: str = ""
    publisher: str = ""
    date: str = ""
    url: str = ""


class PositionItem(BaseModel):
    position_name: str = ""
    company_name: str = ""
    country: str = ""
    start_date: str = ""
    end_date: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    job_type: str = ""
    job_details: str = ""


class EducationItem(BaseModel):
    school_name: str = ""
    school_type: str = ""
    degree_type: str = ""
    faculty_department: str = ""
    specialization_subjects: str = ""
    country: str = ""
    start_date: str = ""
    end_date: str = ""
    learning_mode: str = ""
    education_details: str = ""


class ParsedResumeResult(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    country: str = ""
    language: str = ""
    spoken_languages: List[str] = Field(default_factory=list)
    honors_and_awards: List[str] = Field(default_factory=list)
    courses_and_certifications: List[str] = Field(default_factory=list)
    linkedin_url: str = ""
    github_url: str = ""
    twitter_url: str = ""
    website_url: str = ""
    kaggle_url: str = ""
    date_of_birth: Optional[str] = None
    nationality: str = ""
    summary_objective: str = ""
    work_authorization: str = ""
    # Float, not int: the resume_analyzer fills this from the calculate_yoe
    # tool, which returns years to one decimal place (2.4 = 2 years 4 months).
    years_of_experience: float = 0.0
    has_remote_work_experience: bool = False
    remote_work_type: str = ""
    has_management_experience: bool = False
    management_level: str = ""
    approximate_age: Optional[int] = None
    brief_summary: str = ""
    drivers_licenses: List[str] = Field(default_factory=list)
    interests_hobbies: List[str] = Field(default_factory=list)
    projects: List[ProjectItem] = Field(default_factory=list)
    volunteer_experience: List[str] = Field(default_factory=list)
    publications: List[PublicationItem] = Field(default_factory=list)
    positions: List[PositionItem] = Field(default_factory=list)
    # Skills grouped by the category headings used in the resume (e.g.
    # "Languages", "Frameworks / Libraries"). When the resume lists skills
    # without any category headings, all skills go under a single "Default" key.
    skills: Dict[str, List[str]] = Field(default_factory=dict)
    education_qualifications: List[EducationItem] = Field(default_factory=list)


class ParsedResume(BaseModel):
    id: int
    result: ParsedResumeResult


# ── URL validation & years-of-experience computation ─────────────────────────

# The *_url fields that must hold a valid URL or an empty string.
URL_FIELDS = (
    "linkedin_url", "github_url", "twitter_url", "website_url", "kaggle_url",
)


def is_valid_url(value: str) -> bool:
    """
    Return True if ``value`` is a syntactically valid http(s) URL.

    An empty string is considered valid (a URL that could not be extracted is
    intentionally left blank). Bare link labels ("LinkedIn", "Portfolio",
    "Kaggle", a person's name) are rejected.
    """
    if value == "":
        return True
    from urllib.parse import urlparse

    try:
        parsed = urlparse(value.strip())
    except Exception:  # noqa: BLE001 — any parse failure is an invalid URL
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


def find_invalid_url_fields(result: "ParsedResumeResult") -> List[str]:
    """Return the names of *_url fields on ``result`` that are not valid URLs."""
    return [field for field in URL_FIELDS if not is_valid_url(getattr(result, field, ""))]


def _parse_iso_date(value: Optional[str]):
    """Parse a YYYY-MM-DD string into a ``date``; return None on any failure."""
    if not value:
        return None
    from datetime import date

    try:
        return date.fromisoformat(value.strip()[:10])
    except (ValueError, TypeError):
        return None


def compute_years_of_experience(result: "ParsedResumeResult") -> int:
    """
    Compute total years of professional experience from the position dates.

    The span runs from the earliest position start_date to the latest end_date
    (a missing/blank end_date is treated as today, i.e. a current position),
    and is floored to whole years. Returns 0 when no usable dates exist.
    """
    from datetime import date

    starts, ends = [], []
    for pos in result.positions:
        start = _parse_iso_date(pos.start_date)
        if start is None:
            continue
        starts.append(start)
        # A blank/missing end_date means the position is current → use today.
        end = _parse_iso_date(pos.end_date) or date.today()
        ends.append(end)

    if not starts:
        return 0

    earliest, latest = min(starts), max(ends)
    if latest <= earliest:
        return 0
    return (latest - earliest).days // 365


# JSON template embedded verbatim in the resume-analysis task description.
# Safe for CrewAI description interpolation: interpolation only replaces bare
# {identifier} tokens, and every brace here is followed by a quoted key or
# whitespace. Never add a bare {word} token to this literal — CrewAI would
# raise a KeyError for a missing crew input.
PARSED_RESUME_SCHEMA: str = """{
  "id": <resume_id>,
  "result": {
    "name": "",
    "email": "",
    "phone": "",
    "address": "",
    "city": "",
    "country": "",
    "language": "",
    "spoken_languages": [],
    "honors_and_awards": [],
    "courses_and_certifications": [],
    "linkedin_url": "",
    "github_url": "",
    "twitter_url": "",
    "kaggle_url": "",
    "website_url": "",
    "date_of_birth": null,
    "nationality": "",
    "summary_objective": "",
    "work_authorization": "",
    "years_of_experience": 0.0,
    "has_remote_work_experience": false,
    "remote_work_type": "",
    "has_management_experience": false,
    "management_level": "",
    "approximate_age": null,
    "brief_summary": "",
    "drivers_licenses": [],
    "interests_hobbies": [],
    "projects": [
      {
        "project_name": "",
        "description": "",
        "url": ""
      }
    ],
    "volunteer_experience": [],
    "publications": [
      {
        "title": "",
        "publisher": "",
        "date": "",
        "url": ""
      }
    ],
    "positions": [
      {
        "position_name": "",
        "company_name": "",
        "country": "",
        "start_date": "",
        "end_date": null,
        "skills": [],
        "job_type": "",
        "job_details": ""
      }
    ],
    "skills": {
      "Default": []
    },
    "education_qualifications": [
      {
        "school_name": "",
        "school_type": "",
        "degree_type": "",
        "faculty_department": "",
        "specialization_subjects": "",
        "country": "",
        "start_date": "",
        "end_date": "",
        "learning_mode": "",
        "education_details": ""
      }
    ]
  }
}"""
