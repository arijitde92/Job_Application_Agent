"""
app.services.extractors.text_job_extractor
------------------------------------------
Job details from a job description the user supplied directly — pasted into
the tailoring form, or uploaded as a file — instead of a LinkedIn job URL.

Two steps, mirroring what the LinkedIn extractor produces:

  1. ``extract_job_description_file_text`` turns an uploaded .pdf/.docx/.md/.txt
     file into plain text (reusing the resume parser's per-type extractors).
     The frontend shows that text in the job-description textbox so the user
     can review/edit it before submitting.
  2. ``extract_job_details_from_text`` builds a ``JobDetails`` from the text.
     The user's text is kept verbatim as ``job_description``; an LLM only
     fills in the metadata fields (title, company, location, ...). If the LLM
     call fails the run still proceeds with the metadata left as "N/A" — the
     crew tailors from ``job_description`` either way.
"""

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.services.resume_parser import (
    ResumeParsingError,
    extract_docx_text,
    extract_md_text,
    extract_pdf_text,
)

# JobDetails lives in the LinkedIn extractor, which imports crewai_tools at
# module level; it is imported lazily because app.schemas imports this module
# for the length limits.
if TYPE_CHECKING:
    from app.services.extractors.linkedin_extractor import JobDetails

logger = get_logger(__name__)

ALLOWED_JOB_DESCRIPTION_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}

# Pasted/uploaded descriptions shorter than this are almost certainly not a job
# posting (e.g. a stray title); longer than the max is not a single posting.
MIN_JOB_DESCRIPTION_CHARS = 50
MAX_JOB_DESCRIPTION_CHARS = 50_000


class JobDescriptionParsingError(Exception):
    """Raised when an uploaded job description file cannot be read as text."""


def extract_job_description_file_text(file_path: str) -> str:
    """
    Return the text of a job description file (.pdf, .docx, .md or .txt).

    Raises:
        JobDescriptionParsingError: If the type is unsupported, the file cannot
            be parsed, or it contains no meaningful text.
    """
    suffix = Path(file_path).suffix.lower()
    if suffix not in ALLOWED_JOB_DESCRIPTION_EXTENSIONS:
        raise JobDescriptionParsingError(
            f"Unsupported job description file type '{suffix}'. Supported types: "
            f"{', '.join(sorted(ALLOWED_JOB_DESCRIPTION_EXTENSIONS))}."
        )
    try:
        if suffix == ".pdf":
            text = extract_pdf_text(file_path)
        elif suffix == ".docx":
            text = extract_docx_text(file_path)
        else:  # .md / .txt are both read as plain utf-8 text
            text = extract_md_text(file_path)
    except ResumeParsingError as e:
        raise JobDescriptionParsingError(str(e)) from e

    text = text.strip()
    if len("".join(text.split())) < MIN_JOB_DESCRIPTION_CHARS:
        raise JobDescriptionParsingError(
            "The job description file contains no meaningful text."
        )
    logger.info(
        "text_job_extractor: extracted %d chars from job description file '%s'",
        len(text), file_path,
    )
    return text


# ── Metadata extraction (LLM) ─────────────────────────────────────────────────

class _JobMetadata(BaseModel):
    """The JobDetails fields the LLM fills in; job_description stays verbatim."""

    job_name: str = "N/A"
    company_name: str = "N/A"
    location: str = "N/A"
    workplace_type: str = "N/A"
    seniority_level: str = "N/A"
    employment_type: str = "N/A"
    job_function: str = "N/A"
    industry: str = "N/A"
    about_company: str = "N/A"
    requirements: str = Field(default="N/A")


# LinkedIn's seniority levels — the same vocabulary the LinkedIn extractor
# yields, so downstream tasks see one set of values whatever the job source.
SENIORITY_LEVELS = (
    "Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive",
)

_METADATA_PROMPT = """\
You extract structured metadata from a job posting. Read the job posting below
and return ONE JSON object with exactly these string keys:

  job_name         - the job title / role name
  company_name     - the hiring company's name
  location         - job location (city, country)
  workplace_type   - "On-site", "Remote" or "Hybrid"
  seniority_level  - exactly one of: "Internship", "Entry level", "Associate",
                     "Mid-Senior level", "Director", "Executive", or "N/A"
                     (see the seniority rules below)
  employment_type  - e.g. "Full-time", "Part-time", "Contract", "Internship"
  job_function     - job function / department, e.g. "Engineering"
  industry         - the company's industry / sector
  about_company    - a one or two sentence description of the company
  requirements     - the must-have requirements and qualifications, copied from
                     the posting as a newline-separated list

Rules:
  - Use ONLY information stated in the posting. Never guess or invent values.
  - Use the string "N/A" for any field the posting does not state.
  - seniority_level is the one field you may DERIVE. Apply the first rule that
    matches, and never pick a level from the company, industry or tech stack:
      1. The posting states a seniority / experience level (e.g. "Seniority:
         Senior", "Experience level: Entry") -> map it to the closest level.
      2. The job title has a seniority keyword:
           Intern, Trainee, Apprentice                        -> "Internship"
           Junior, Graduate, Fresher, Entry                   -> "Entry level"
           Associate                                          -> "Associate"
           Senior, Sr., Lead, Staff, Principal, Manager       -> "Mid-Senior level"
           Director, Head of                                  -> "Director"
           VP, Vice President, Chief, C-level (CTO, CEO, ...) -> "Executive"
      3. The posting states the required years of experience -> use the
         minimum: 0-1 years "Entry level", 2-4 years "Associate", 5 or more
         years "Mid-Senior level".
      4. Otherwise "N/A".
  - Return only the JSON object: no markdown fences, no commentary.

Job posting:
<<<
{text}
>>>
"""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _call_metadata_llm(prompt: str) -> str:
    """Send ``prompt`` to the metadata-extraction LLM and return its reply."""
    from crewai import LLM

    llm = LLM(
        model="gemini/gemini-3.5-flash-lite",
        api_key=os.environ.get("GEMINI_API_KEY"),
        temperature=0.0,
        max_tokens=4000,
    )
    return str(llm.call([{"role": "user", "content": prompt}]))


def _parse_metadata(reply: str) -> _JobMetadata:
    """Parse the LLM reply into ``_JobMetadata``, normalising blanks to "N/A"."""
    data = json.loads(_FENCE_RE.sub("", reply.strip()))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    cleaned = {}
    for key in _JobMetadata.model_fields:
        value = data.get(key)
        if isinstance(value, list):
            value = "\n".join(str(v) for v in value)
        value = str(value).strip() if value is not None else ""
        cleaned[key] = value or "N/A"
    cleaned["seniority_level"] = _normalise_seniority(cleaned["seniority_level"])
    return _JobMetadata(**cleaned)


def _normalise_seniority(value: str) -> str:
    """Map ``value`` onto SENIORITY_LEVELS (ignoring case, hyphens and spacing);
    anything else — e.g. the job title echoed back — becomes "N/A"."""
    def key(v: str) -> str:
        return "".join(v.lower().replace("-", " ").split())

    for level in SENIORITY_LEVELS:
        if key(value) == key(level):
            return level
    if value != "N/A":
        logger.info(
            "text_job_extractor: discarding non-standard seniority_level %r", value
        )
    return "N/A"


def extract_job_details_from_text(text: str) -> "JobDetails":
    """
    Build ``JobDetails`` from a user-supplied job description.

    ``job_description`` is the user's text verbatim. The remaining fields come
    from an LLM; on any LLM or parsing failure they stay "N/A" and a warning
    is logged — a missing company name must not fail the whole tailoring run.
    """
    from app.services.extractors.linkedin_extractor import JobDetails

    text = text.strip()
    try:
        metadata = _parse_metadata(_call_metadata_llm(_METADATA_PROMPT.format(text=text)))
        logger.info(
            "text_job_extractor: extracted metadata — job '%s' at '%s'",
            metadata.job_name, metadata.company_name,
        )
    except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
        logger.warning(
            "text_job_extractor: could not parse LLM metadata reply (%s); "
            "continuing with N/A metadata", e,
        )
        metadata = _JobMetadata()
    except Exception as e:  # noqa: BLE001 — LLM/network failure must not fail the job
        logger.warning(
            "text_job_extractor: metadata LLM call failed (%s); continuing with "
            "N/A metadata", e, exc_info=True,
        )
        metadata = _JobMetadata()

    return JobDetails(url="", job_description=text, **metadata.model_dump())
