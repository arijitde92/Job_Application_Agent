"""
app.services.pipeline.guardrails
--------------------------------
LLM-as-judge faithfulness check for the resume analyzer's output.

The judge scores how well the parsed-resume JSON is grounded in the extracted
resume text, from 0 (largely invented) to 10 (every value traceable). The
resume-analysis guardrail middleware rejects a score below
``FAITHFULNESS_THRESHOLD`` and sends the judge's feedback back to the agent
for another extraction attempt.

The judge runs on Claude with native structured output (``json_schema``),
which — unlike forced tool calling — is compatible with Sonnet 5's adaptive
thinking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable

logger = get_logger(__name__)

# Minimum faithfulness score (0-10) the parsed JSON must reach.
FAITHFULNESS_THRESHOLD = 7

_JUDGE_SYSTEM_PROMPT = """\
You are a strict but fair auditor of structured data extracted from resumes.
You receive the REFERENCE resume text and a PARSED JSON document an extraction
agent produced from it. Decide whether every value in the JSON is supported by
the reference text.

Count as UNSUPPORTED (hallucinated):
  - employers, job titles, institutions, degrees, projects, publications,
    certifications, skills, contact details, URLs, metrics or dates that do
    not appear in the reference text;
  - values that contradict the reference text.

Do NOT count as unsupported:
  - empty values ("", null, [], false, 0) — leaving a field blank is allowed;
  - dates rewritten into YYYY-MM-DD form (a month/year resolves to its first day);
  - "id" and "years_of_experience" (system-assigned / computed by a tool);
  - "brief_summary", which is a faithful paraphrase of the resume by design;
  - derived fields (country from a location, has_remote_work_experience,
    remote_work_type, has_management_experience, management_level) when they
    are a reasonable reading of the text;
  - skills split out of a grouped list, or skill category headings copied
    from the resume, or a "Default" category.

Score 10 when every value is supported, 7-9 for minor slips, 4-6 when some
facts are invented, 0-3 when much of the document is invented. List each
unsupported value in unsupported_claims, and write feedback telling the agent
exactly which values to fix or remove."""


class FaithfulnessVerdict(BaseModel):
    """The judge's structured verdict on one parsed-resume document."""

    score: int = Field(
        ge=0, le=10,
        description="0 = largely invented, 10 = every value is supported by the resume.",
    )
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="Each JSON value that is not supported by the resume text.",
    )
    feedback: str = Field(
        default="",
        description="Actionable instructions for the extraction agent.",
    )


class FaithfulnessJudge:
    """
    Score a parsed-resume JSON document's faithfulness to the resume text.

    Args:
        model: Chat model that supports native structured output.
        threshold: Minimum passing score (0-10).
    """

    def __init__(self, model: "BaseChatModel", threshold: int = FAITHFULNESS_THRESHOLD) -> None:
        self.threshold = threshold
        self._judge: Runnable = model.with_structured_output(
            FaithfulnessVerdict, method="json_schema"
        ).with_config(run_name="faithfulness_judge")

    def evaluate(self, *, reference: str, output: str) -> FaithfulnessVerdict:
        """
        Judge ``output`` against ``reference``.

        Args:
            reference: The extracted resume text.
            output: The parsed-resume JSON to audit.

        Returns:
            The judge's verdict.
        """
        verdict = self._judge.invoke([
            SystemMessage(_JUDGE_SYSTEM_PROMPT),
            HumanMessage(
                f"REFERENCE resume text:\n<<<\n{reference}\n>>>\n\n"
                f"PARSED JSON:\n<<<\n{output}\n>>>"
            ),
        ])
        if not isinstance(verdict, FaithfulnessVerdict):
            verdict = FaithfulnessVerdict.model_validate(verdict)
        logger.info(
            "guardrails: faithfulness score %d/10 (%d unsupported claim(s))",
            verdict.score, len(verdict.unsupported_claims),
        )
        return verdict

    def passes(self, verdict: FaithfulnessVerdict) -> bool:
        """True when ``verdict`` meets the threshold."""
        return verdict.score >= self.threshold
