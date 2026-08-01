"""
app.services.crew.docx_tools
----------------------------
Per-run tool factory for the resume_strategist agent's .docx generation step.

The generate_resume_docx tool POSTs the tailored resume content to an external
document-rendering service (``RESUME_DOCX_SERVICE_URL``) which returns a
polished Word (.docx) resume. Like the save tool in
``app.services.crew.resume_tools``, it is built fresh for every crew run so
the job identity and the output path are closured into the tool rather than
supplied by the LLM, and so a shared ``ResumeDocxContext.state`` dict can
carry the outcome to the crew runner (which converts the docx to PDF and
uploads both).

Responsibility split (user decision): the agent supplies ONLY the ``resume``
object — personal information, summary, experience, and so on. The tool wraps
it deterministically in the service's ``metadata`` envelope (request_id,
template, document_name, page_size, section_order), which keeps the one part
of the payload the service is strict about out of the LLM's hands.

The agent may retry a failed call, but never more than ``_MAX_ATTEMPTS``
times in total — the tool enforces the cap itself via ``ctx.state`` and its
terminal error tells the agent to stop and fall back to the Markdown resume.

CrewAI converts tool exceptions into error messages fed back to the LLM, so
this tool never relies on raising: every failure returns an "ERROR: ..."
string the agent can react to.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict

import requests
from crewai.tools import tool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.crew.resume_tools import _strip_code_fences

logger = get_logger(__name__)

# Section order the service renders when every section is present. The tool
# filters this to the sections the agent actually supplied; personal_information
# is deliberately absent — it is the document header, not an ordered section.
_DEFAULT_SECTION_ORDER = (
    "summary",
    "experience",
    "education",
    "skills",
    "publications",
    "projects",
    "certifications",
)

# Total attempts (successful or not) the agent gets before the tool refuses
# further calls and the run falls back to the Markdown-only flow.
_MAX_ATTEMPTS = 3

# A .docx file is a ZIP archive, so a genuine response always starts with the
# ZIP local-file-header magic.
_DOCX_MAGIC = b"PK"


@dataclass
class ResumeDocxContext:
    """Per-run identity and shared state for the docx-generation tool."""

    applicant_name: str
    company_name: str
    job_name: str
    job_id: int
    # Local path the generated .docx is written to; chosen by the crew runner
    # (a temp file for the web app) so the LLM never picks filesystem paths.
    output_path: str
    # Human display name ("First Last") used for metadata.document_name; the
    # applicant_name is an email-derived slug used for filenames.
    display_name: str = ""
    state: Dict[str, Any] = field(default_factory=dict)


def sanitize_filename_component(text: str, max_len: int = 60) -> str:
    """Reduce one filename component to safe characters, or 'resume' if empty."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned[:max_len] if cleaned else "resume"


def build_docx_filename(applicant_name: str, company_name: str, job_name: str) -> str:
    """The generated resume's filename: {applicant}_{company}_{job}_resume.docx."""
    return (
        f"{sanitize_filename_component(applicant_name)}"
        f"_{sanitize_filename_component(company_name)}"
        f"_{sanitize_filename_component(job_name)}_resume.docx"
    )


def _coerce_resume_object(resume_json: Any) -> dict:
    """
    Return ``resume_json`` as a dict, JSON-decoding a (possibly fenced) string.

    Raises ValueError with an agent-actionable message on anything else.
    """
    if isinstance(resume_json, dict):
        return resume_json
    if isinstance(resume_json, str):
        try:
            decoded = json.loads(_strip_code_fences(resume_json))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"resume_json is not valid JSON ({e}). Pass ONE compact JSON "
                "object (no markdown fences, no comments, no trailing commas)."
            ) from e
        if not isinstance(decoded, dict):
            raise ValueError(
                "resume_json decoded to a "
                f"{type(decoded).__name__}, but it must be a JSON object with "
                'keys like "personal_information", "summary", "experience".'
            )
        return decoded
    raise ValueError(
        f"resume_json must be a JSON object (or its string form), got "
        f"{type(resume_json).__name__}."
    )


def _build_payload(resume: dict, ctx: ResumeDocxContext, attempt: int) -> dict:
    """Wrap the agent's resume object in the service's metadata envelope."""
    return {
        "metadata": {
            "request_id": f"job-{ctx.job_id}-attempt-{attempt}",
            "template": "modern",
            "document_name": f"{ctx.display_name or ctx.applicant_name} Resume",
            "page_size": "letter",
            "section_order": [s for s in _DEFAULT_SECTION_ORDER if resume.get(s)],
        },
        "resume": resume,
    }


def make_generate_resume_docx_tool(ctx: ResumeDocxContext):
    """Build the generate_resume_docx tool bound to one run's ``ctx``."""
    settings = get_settings()

    @tool("generate_resume_docx")
    def generate_resume_docx(resume_json: str) -> str:
        """
        Render the tailored resume as a polished Word (.docx) document via the
        external document-generation service. Call this exactly once, AFTER you
        have finished tailoring the resume content, with the complete resume
        content as ONE compact JSON object. Metadata (template, page size,
        document name, section order) is added automatically — pass ONLY the
        resume content object.

        Args:
            resume_json (str): A single compact JSON object with these keys
                (omit any section the applicant has no content for):
                "personal_information" (object: name, location, email, phone,
                github, linkedin), "summary" (string), "experience" (list of
                objects: job_title, company, location, work_mode, start_date,
                end_date, bullets), "education" (list of objects: degree,
                institution, location, gpa, start_date, end_date), "skills"
                (LIST of objects, each with "category" and "skills" keys),
                "publications" (list of objects with "citation"), "projects"
                (list of objects: name, url, bullets), "certifications" (list
                of objects: name, issuer, year, url). **bold** markers are
                allowed inside bullet strings.

        Returns:
            str: "SUCCESS: ..." when the .docx was generated and saved,
                 otherwise "ERROR: <what went wrong and how to fix it>". If the
                 ERROR says to stop calling this tool, do NOT call it again —
                 output the tailored resume in Markdown as your final answer.
        """
        attempts = ctx.state.get("attempts", 0)
        if attempts >= _MAX_ATTEMPTS:
            logger.warning(
                "docx_tools: attempt cap reached for job %d — refusing call %d",
                ctx.job_id, attempts + 1,
            )
            return (
                f"ERROR: The document service failed {_MAX_ATTEMPTS} times. Do "
                "NOT call generate_resume_docx again. Output the complete "
                "tailored resume in Markdown as your final answer instead."
            )
        attempt = attempts + 1
        ctx.state["attempts"] = attempt
        remaining = _MAX_ATTEMPTS - attempt

        # 1. Coerce/validate the agent's resume object. A malformed input
        # consumes an attempt — keeps the cap simple and bounded.
        try:
            resume = _coerce_resume_object(resume_json)
        except ValueError as e:
            logger.warning(
                "docx_tools: invalid resume_json for job %d (attempt %d/%d): %s",
                ctx.job_id, attempt, _MAX_ATTEMPTS, e,
            )
            return f"ERROR: {e} You have {remaining} attempt(s) left."

        personal = resume.get("personal_information")
        if not (isinstance(personal, dict) and str(personal.get("name", "")).strip()):
            return (
                'ERROR: resume_json must contain a "personal_information" object '
                'with a non-empty "name". Copy it exactly from the parsed resume '
                f"JSON in your context. You have {remaining} attempt(s) left."
            )
        if not any(resume.get(section) for section in _DEFAULT_SECTION_ORDER):
            return (
                "ERROR: resume_json has no content sections. Include at least "
                '"summary", "experience", "education" and "skills" with the '
                f"tailored content. You have {remaining} attempt(s) left."
            )

        payload = _build_payload(resume, ctx, attempt)

        # 2. POST to the document service.
        try:
            response = requests.post(
                settings.RESUME_DOCX_SERVICE_URL,
                json=payload,
                timeout=(10, settings.RESUME_DOCX_TIMEOUT_SECONDS),
            )
        except requests.RequestException as e:
            logger.warning(
                "docx_tools: docx service request failed for job %d "
                "(attempt %d/%d): %s", ctx.job_id, attempt, _MAX_ATTEMPTS, e,
            )
            return (
                f"ERROR: Could not reach the document service: {e}. Call "
                f"generate_resume_docx again with the same resume_json. You "
                f"have {remaining} attempt(s) left."
            )

        if response.status_code != 200:
            logger.warning(
                "docx_tools: docx service returned %d for job %d (attempt %d/%d): %s",
                response.status_code, ctx.job_id, attempt, _MAX_ATTEMPTS,
                response.text[:300],
            )
            return (
                f"ERROR: The document service returned HTTP "
                f"{response.status_code}: {response.text[:300]}. Fix the "
                f"reported problem in resume_json and call the tool again. You "
                f"have {remaining} attempt(s) left."
            )
        if not response.content.startswith(_DOCX_MAGIC):
            logger.warning(
                "docx_tools: docx service response is not a .docx for job %d "
                "(attempt %d/%d, %d bytes)",
                ctx.job_id, attempt, _MAX_ATTEMPTS, len(response.content),
            )
            return (
                "ERROR: The document service did not return a .docx file. Call "
                f"generate_resume_docx again. You have {remaining} attempt(s) left."
            )

        # 3. Save the .docx locally; the crew runner picks it up from ctx.state.
        try:
            with open(ctx.output_path, "wb") as f:
                f.write(response.content)
        except OSError as e:
            logger.error(
                "docx_tools: failed to write .docx to '%s' for job %d: %s",
                ctx.output_path, ctx.job_id, e, exc_info=True,
            )
            return (
                f"ERROR: The generated .docx could not be saved locally: {e}. "
                f"Call generate_resume_docx again. You have {remaining} "
                "attempt(s) left."
            )

        ctx.state["docx_path"] = ctx.output_path
        logger.info(
            "docx_tools: .docx resume generated for job %d (%d bytes) → %s",
            ctx.job_id, len(response.content), ctx.output_path,
        )
        return (
            "SUCCESS: The .docx resume was generated and saved. Do NOT call "
            "generate_resume_docx again. Now output the complete tailored "
            "resume in Markdown as your final answer."
        )

    return generate_resume_docx
