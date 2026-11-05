"""
Answer generation: run the resume_analyzer agent on a golden sample.

Mirrors the isolation harness in tests/run_resume_analyzer_test.py: it calls
``app.services.pipeline.analyze_resume`` — the exact LangChain agent, task
prompt and guardrail middleware the pipeline's ``resume_analysis`` node runs —
with ``local_only=True`` (no GCS, no DB). The sample's context text
(already-extracted resume text) is written to a temp ``.md`` file that the
agent's ``read_resume`` tool reads; the normalized parsed JSON is read back
from ``ctx.state["json"]``.

The agent and its faithfulness judge run on Anthropic Claude
(``app.services.pipeline.llms.CLAUDE_MODEL``) and therefore need
ANTHROPIC_API_KEY. With LANGSMITH_TRACING=true each sample becomes a trace
tagged "ragas".
"""

import os
import tempfile
from pathlib import Path

from tests.ragas.common.dataset import GoldenSample

BACKEND_DIR = Path(__file__).resolve().parents[4]


def render_task_prompt() -> str:
    """
    Render the resume-analysis task description exactly as the agent sees it
    (resume_id 0, the id used for all golden samples). It is identical for
    every sample and every run.
    """
    from app.services.pipeline.prompts import resume_analysis_description

    return resume_analysis_description(0)


def generate_answer(sample: GoldenSample) -> str:
    """
    Run the resume_analyzer on ``sample`` and return the normalized parsed
    JSON (the guardrail-validated ``ctx.state["json"]``).

    Raises:
        RuntimeError: If the agent finishes without a successful save.
    """
    from app.services.pipeline import analyze_resume, build_run_config
    from app.services.pipeline.tools import ResumeAnalysisContext

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
        config = build_run_config(
            include_github=False, extra_tags=["ragas"],
            extra_metadata={"sample_id": sample.sample_id},
        )
        try:
            return analyze_resume(ctx, tmp_md.name, config=config)
        except Exception as e:
            raise RuntimeError(
                f"resume_analyzer never saved a parsed resume for sample "
                f"'{sample.sample_id}': {e}"
            ) from e
    finally:
        if os.path.exists(tmp_md.name):
            os.unlink(tmp_md.name)
