"""
app.services.crew.crew
-----------------------
Crew assembly for the job-application pipeline.

``build_crew()`` wires the four agents and their tasks into a CrewAI ``Crew``.
The web app (``app.services.crew_runner``) and the standalone CLI
(``python -m app.services.crew``) both construct the crew through this factory.
"""

from crewai import Crew

from app.core.logging import get_logger
from app.services.crew.agents import (
    github_project_summarizer,
    profiler,
    resume_strategist,
    interview_preparer,
)
from app.services.crew.tasks import (
    github_summary_task,
    profile_task,
    resume_strategy_task,
    interview_preparation_task,
)

logger = get_logger(__name__)


def build_crew(output_log_file: str = "crew_log.txt", verbose: bool = True, tracing: bool = True) -> Crew:
    """
    Construct the job-application :class:`crewai.Crew`.

    Job details are extracted once by the caller and injected into every task via
    the ``job_details_json`` crew input variable, so no researcher agent is needed.

    Args:
        output_log_file: Path for CrewAI's verbose execution log.
        verbose: Enable verbose agent/task logging.
        tracing: Enable CrewAI tracing.

    Returns:
        A configured :class:`crewai.Crew` ready for ``kickoff(inputs=...)``.
    """
    return Crew(
        agents=[
            github_project_summarizer,
            profiler,
            resume_strategist,
            interview_preparer,
        ],
        tasks=[
            github_summary_task,
            profile_task,
            resume_strategy_task,
            interview_preparation_task,
        ],
        output_log_file=output_log_file,
        tracing=tracing,
        verbose=verbose,
    )
