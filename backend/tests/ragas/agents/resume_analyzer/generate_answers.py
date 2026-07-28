"""
Answer generation: run the resume_analyzer agent on a golden sample.

Mirrors the isolation harness in tests/run_resume_analyzer_test.py — a
one-agent, one-task crew with ``local_only=True`` (no GCS, no DB). The
sample's context text (already-extracted resume text) is written to a temp
``.md`` file and passed as the ``resume_path`` crew input; the normalized
parsed JSON is read back from ``ctx.state["json"]``.

The agent itself runs on Anthropic Claude (``claude_llm_extraction`` in
app/services/crew/agents.py) and therefore needs ANTHROPIC_API_KEY.
"""

import os
import tempfile
from pathlib import Path

from tests.ragas.common.dataset import GoldenSample

BACKEND_DIR = Path(__file__).resolve().parents[4]

# The {resume_path} crew-input token is a per-run temp file; the rendered
# RAGAS "question" replaces it with this stable placeholder so the prompt is
# identical for every sample and every run.
RESUME_PATH_PLACEHOLDER = "<resume file>"


def render_task_prompt() -> str:
    """
    Render the resume-analysis task description exactly as the agent sees it
    (resume_id 0, the id used for all golden samples), with the
    ``{resume_path}`` input token replaced by a stable placeholder.
    """
    from app.services.crew.resume_tools import ResumeAnalysisContext
    from app.services.crew.tasks import _resume_analysis_task

    ctx = ResumeAnalysisContext(
        user_id=0, resume_id=0, user_name="ragas_eval", local_only=True
    )
    task = _resume_analysis_task(ctx, run_async=False)
    return task.description.replace("{resume_path}", RESUME_PATH_PLACEHOLDER)


def generate_answer(sample: GoldenSample) -> str:
    """
    Run the resume_analyzer on ``sample`` and return the normalized parsed
    JSON (the guardrail-validated ``ctx.state["json"]``).

    Raises:
        RuntimeError: If the agent finishes without a successful save.
    """
    from crewai import Crew

    from app.services.crew.agents import resume_analyzer
    from app.services.crew.resume_tools import ResumeAnalysisContext
    from app.services.crew.tasks import _resume_analysis_task

    tmp_md = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="ragas_resume_", delete=False, encoding="utf-8"
    )
    with tmp_md as f:
        f.write(sample.context_text)

    try:
        ctx = ResumeAnalysisContext(
            user_id=0,
            resume_id=0,
            user_name="ragas_eval",
            local_only=True,
            resume_text=sample.context_text,
        )
        task = _resume_analysis_task(ctx, run_async=False)
        crew = Crew(agents=[resume_analyzer], tasks=[task], verbose=False)
        crew.kickoff(inputs={"resume_path": tmp_md.name})

        if not (ctx.state.get("saved") and ctx.state.get("json")):
            raise RuntimeError(
                f"resume_analyzer never saved a parsed resume for sample "
                f"'{sample.sample_id}'."
            )
        return ctx.state["json"]
    finally:
        if os.path.exists(tmp_md.name):
            os.unlink(tmp_md.name)
