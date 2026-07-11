"""
app.services.crew.resume_tools
------------------------------
Per-run tool and guardrail factories for the resume_analyzer agent.

The save tool is built fresh for every crew run (like the Task objects in
``app.services.crew.tasks``) so that user_id / resume_id / user_name are
closured into the tool rather than supplied by the LLM, and so a shared
``ResumeAnalysisContext.state`` dict can carry the outcome to the task
guardrail. The tool enforces the required ordering in code:

    validate JSON → write temp file → upload to GCS → update database

The database is only touched after the file write and the GCS upload have
both succeeded. With ``local_only=True`` (isolation test script, CLI) the
tool stops after the temp-file write — no GCS, no database.

CrewAI converts tool exceptions into error messages fed back to the LLM, so
this tool never relies on raising: every failure returns an "ERROR: ..."
string the agent can react to, and the task guardrail aborts the crew if no
successful save happened after the retry budget.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from crewai import TaskOutput
from crewai.tools import tool
from pydantic import ValidationError

from app.core.logging import get_logger
from app.services.resume_parser import (
    ParsedResume,
    build_parsed_resume_filename,
    compute_years_of_experience,
    find_invalid_url_fields,
)

logger = get_logger(__name__)


@dataclass
class ResumeAnalysisContext:
    """Per-run identity and shared state for the resume-analysis task."""

    user_id: int
    resume_id: int
    user_name: str
    local_only: bool = False
    # The extracted resume text, used as the reference context for the
    # hallucination guardrail so the agent cannot invent skills/positions/etc.
    resume_text: str = ""
    state: Dict[str, Any] = field(default_factory=dict)


def _strip_code_fences(text: str) -> str:
    """Remove a surrounding ```json ... ``` / ``` ... ``` fence if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def make_save_parsed_resume_tool(ctx: ResumeAnalysisContext):
    """Build the save_parsed_resume tool bound to one run's ``ctx``."""

    @tool("save_parsed_resume")
    def save_parsed_resume(parsed_resume_json: str) -> str:
        """
        Persist the parsed resume JSON. Validates the JSON against the required
        schema, writes it to a local file, uploads it to cloud storage, and
        records the storage path in the database. Call this exactly once with
        the COMPLETE parsed resume JSON string (the full {"id": ..., "result":
        {...}} document). If the response starts with "ERROR:", fix the
        reported problem and call the tool again with the corrected JSON.

        Args:
            parsed_resume_json (str): The complete parsed resume JSON string.

        Returns:
            str: "SUCCESS: ..." when everything was persisted, otherwise
                 "ERROR: <what went wrong and how to fix it>".
        """
        raw = _strip_code_fences(parsed_resume_json)

        # 1. Validate against the required schema.
        try:
            parsed = ParsedResume.model_validate_json(raw)
        except ValidationError as e:
            logger.warning(
                "resume_tools: invalid parsed-resume JSON for resume %d: %s",
                ctx.resume_id, e,
            )
            return (
                "ERROR: The JSON is invalid or does not match the required schema: "
                f"{e}. Fix the JSON and call save_parsed_resume again with the "
                "complete corrected document."
            )

        # 2. The id is not the LLM's to choose.
        parsed.id = ctx.resume_id

        # 2a. Reject invalid URL fields so the agent re-extracts (or blanks them).
        invalid_urls = find_invalid_url_fields(parsed.result)
        if invalid_urls:
            logger.warning(
                "resume_tools: invalid URL field(s) %s for resume %d",
                invalid_urls, ctx.resume_id,
            )
            details = ", ".join(
                f"{field}={getattr(parsed.result, field)!r}" for field in invalid_urls
            )
            return (
                f"ERROR: These URL fields are not valid URLs: {details}. Every "
                "*_url field must be a complete http(s) URL (e.g. "
                "'https://www.linkedin.com/in/username') or an empty string \"\". "
                "If the resume only shows the link's label/anchor text (like "
                "'LinkedIn', 'Portfolio', 'Kaggle', or a name) and not the actual "
                "URL, set that field to \"\". Fix these and call save_parsed_resume "
                "again."
            )

        # 2b. Compute years_of_experience from position dates when the agent did
        # not extract a value from the summary/objective (left it at 0).
        if not parsed.result.years_of_experience:
            computed = compute_years_of_experience(parsed.result)
            if computed:
                logger.info(
                    "resume_tools: computed years_of_experience=%d from positions "
                    "for resume %d", computed, ctx.resume_id,
                )
            parsed.result.years_of_experience = computed

        normalized = parsed.model_dump_json(indent=2)

        # 3. Write to local temp storage.
        filename = build_parsed_resume_filename(ctx.user_name, ctx.user_id, ctx.resume_id)
        local_path = os.path.join(tempfile.gettempdir(), filename)
        try:
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(normalized)
        except OSError as e:
            logger.error(
                "resume_tools: failed to write parsed resume to '%s': %s",
                local_path, e, exc_info=True,
            )
            return f"ERROR: Could not write the parsed resume file locally: {e}."
        logger.info(
            "resume_tools: parsed resume written to '%s' (%d bytes)",
            local_path, len(normalized.encode("utf-8")),
        )

        if ctx.local_only:
            ctx.state.update({"saved": True, "local_path": local_path, "json": normalized})
            logger.info(
                "resume_tools: local_only run — skipping GCS upload and DB update "
                "for resume %d", ctx.resume_id,
            )
            return f"SUCCESS: Parsed resume saved locally to {local_path}."

        # 4. Upload to GCS. The database is not touched unless this succeeds.
        try:
            from app.services.gcs_service import upload_parsed_resume

            gcs_path = upload_parsed_resume(ctx.user_id, filename, normalized)
        except Exception as e:  # noqa: BLE001 — surface any GCS failure to the agent
            logger.error(
                "resume_tools: GCS upload of parsed resume for resume %d failed: %s",
                ctx.resume_id, e, exc_info=True,
            )
            return (
                "ERROR: The parsed resume file was created but the upload to cloud "
                f"storage failed: {e}. Call save_parsed_resume again to retry."
            )
        logger.info("resume_tools: parsed resume uploaded to %s", gcs_path)

        # 5. Record the GCS path on the Resume row (only after upload success).
        try:
            from sqlalchemy import update
            from sqlalchemy.orm import Session

            from app.core.database import get_sync_engine
            from app.models import Resume

            with Session(get_sync_engine()) as session:
                session.execute(
                    update(Resume)
                    .where(Resume.id == ctx.resume_id)
                    .values(parsed_resume_path=gcs_path)
                )
                session.commit()
        except Exception as e:  # noqa: BLE001 — surface any DB failure to the agent
            logger.error(
                "resume_tools: DB update of parsed_resume_path for resume %d failed: %s",
                ctx.resume_id, e, exc_info=True,
            )
            return (
                "ERROR: The parsed resume was uploaded but recording it in the "
                f"database failed: {e}. Call save_parsed_resume again to retry."
            )
        logger.info(
            "resume_tools: resumes.parsed_resume_path set to '%s' for resume %d",
            gcs_path, ctx.resume_id,
        )

        ctx.state.update({
            "saved": True,
            "local_path": local_path,
            "gcs_path": gcs_path,
            "json": normalized,
        })
        return (
            f"SUCCESS: Parsed resume saved to {gcs_path} and recorded in the database."
        )

    return save_parsed_resume


# Faithfulness score (0-10) the parsed JSON must meet against the resume text.
_HALLUCINATION_THRESHOLD = 7


def _make_hallucination_guardrail(ctx: ResumeAnalysisContext):
    """
    Build a CrewAI HallucinationGuardrail bound to this run's resume text, or
    ``None`` when no reference text / LLM is available (e.g. the CLI path).

    The guardrail scores the parsed JSON's faithfulness against the extracted
    resume text so the agent cannot invent skills, positions, or other facts.

    NOTE: in the open-source crewai package this guardrail is a pass-through
    (it logs a notice and returns the output unchanged) — real faithfulness
    scoring runs only on the CrewAI enterprise platform (app.crewai.com). The
    wiring here is intentionally forward-compatible: it activates automatically
    when running against enterprise, and never rejects a correctly-saved resume
    on open source.
    """
    if not ctx.resume_text:
        return None
    try:
        from crewai.tasks.hallucination_guardrail import HallucinationGuardrail

        from app.services.crew.agents import gemini_llm_extraction

        return HallucinationGuardrail(
            context=ctx.resume_text,
            llm=gemini_llm_extraction,
            threshold=_HALLUCINATION_THRESHOLD,
        )
    except Exception as e:  # noqa: BLE001 — never let guardrail setup break the run
        logger.warning(
            "resume_tools: could not build hallucination guardrail for resume %d: %s",
            ctx.resume_id, e,
        )
        return None


def make_parsed_resume_guardrail(
    ctx: ResumeAnalysisContext,
) -> Callable[[TaskOutput], tuple[bool, Any]]:
    """
    Build the resume-analysis task guardrail bound to one run's ``ctx``.

    A task's ``guardrail`` accepts a single value, so this composes three checks:

      1. The save-tool invariant — the task passes only when save_parsed_resume
         reported success. This replaces the task output with the normalized
         JSON so downstream tasks (profiler) receive the exact persisted
         document, and — on failure — is what aborts the crew after the retry
         budget is exhausted (CrewAI raises, ``kickoff()`` propagates it).
      2. A URL-format check — every *_url field on the saved JSON must be a
         valid http(s) URL or empty. (The save tool blocks invalid URLs before
         it marks the run saved, so this is a secondary net.)
      3. A CrewAI HallucinationGuardrail — once the save is confirmed, the
         normalized JSON is scored for faithfulness against the extracted
         resume text so the agent cannot invent skills, positions, or other
         facts. A failed check returns feedback for a retry; a guardrail
         *error* (e.g. LLM unavailable) fails open so a transient outage does
         not block a correctly-saved resume.
    """
    hallucination_guardrail = _make_hallucination_guardrail(ctx)

    def parsed_resume_guardrail(task_output: TaskOutput) -> tuple[bool, Any]:
        # 1. Save-tool invariant (load-bearing — aborts the crew on failure).
        if not (ctx.state.get("saved") and ctx.state.get("json")):
            logger.warning(
                "resume_tools: guardrail rejected resume-analysis output for resume "
                "%d — no successful save recorded", ctx.resume_id,
            )
            return (
                False,
                "The parsed resume was NOT saved. You MUST call the save_parsed_resume "
                "tool with the complete parsed resume JSON and get a SUCCESS response "
                "before producing your final answer. Fix any ERROR the tool reports "
                "and try again.",
            )

        normalized = ctx.state["json"]

        # 2. URL-format check on the saved JSON (secondary net; the save tool
        # already blocks invalid URLs).
        try:
            saved = ParsedResume.model_validate_json(normalized)
            invalid_urls = find_invalid_url_fields(saved.result)
        except ValidationError:
            invalid_urls = []
        if invalid_urls:
            logger.warning(
                "resume_tools: guardrail found invalid URL field(s) %s for resume %d",
                invalid_urls, ctx.resume_id,
            )
            return (
                False,
                f"These URL fields are not valid URLs: {', '.join(invalid_urls)}. "
                "Every *_url field must be a complete http(s) URL or an empty "
                "string \"\". If only the link's label/anchor text is visible (not "
                "the actual URL), set that field to \"\". Fix these and call "
                "save_parsed_resume again.",
            )

        # 3. Hallucination check against the resume text.
        if hallucination_guardrail is not None:
            try:
                validated_output = TaskOutput(
                    description=task_output.description,
                    raw=normalized,
                    agent=task_output.agent,
                )
                is_faithful, feedback = hallucination_guardrail(validated_output)
            except Exception as e:  # noqa: BLE001 — fail open on guardrail errors
                logger.warning(
                    "resume_tools: hallucination guardrail errored for resume %d "
                    "(passing the saved output through): %s", ctx.resume_id, e,
                )
                return True, normalized
            if not is_faithful:
                logger.warning(
                    "resume_tools: hallucination guardrail flagged resume %d: %s",
                    ctx.resume_id, feedback,
                )
                return (
                    False,
                    "The extracted data was not fully grounded in the resume. "
                    f"{feedback} Re-extract using ONLY facts present in the resume, "
                    "then call save_parsed_resume again with the corrected JSON.",
                )

        return True, normalized

    return parsed_resume_guardrail
