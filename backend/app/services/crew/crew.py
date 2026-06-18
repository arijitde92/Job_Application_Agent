"""
app.services.crew.crew
-----------------------
Crew assembly for the job-application pipeline.

``build_crew()`` wires the agents and their tasks into a CrewAI ``Crew``.
The web app (``app.services.crew_runner``) and the standalone CLI
(``python -m app.services.crew``) both construct the crew through this factory.

When ``include_github`` is False (the applicant has no GitHub profile) the
GitHub summarizer agent and task are omitted, and the remaining agents work
from the resume + job details alone with explicit grounding rules (see
``app.services.crew.tasks``).
"""

from crewai import Crew

from app.core.logging import get_logger
from app.services.crew.agents import (
    github_project_summarizer,
    profiler,
    resume_strategist,
    interview_preparer,
)
from app.services.crew.tasks import build_tasks

logger = get_logger(__name__)


def build_crew(
    output_log_file: str = "crew_log.txt",
    verbose: bool = True,
    tracing: bool = True,
    include_github: bool = True,
) -> Crew:
    """
    Construct the job-application :class:`crewai.Crew`.

    Job details are extracted once by the caller and injected into every task via
    the ``job_details_json`` crew input variable, so no researcher agent is needed.

    Args:
        output_log_file: Path for CrewAI's verbose execution log.
        verbose: Enable verbose agent/task logging.
        tracing: Enable CrewAI tracing.
        include_github: When True, include the GitHub summarizer agent/task and
            require the ``github_url`` / ``bq_dataset_name`` crew inputs. When
            False, skip GitHub entirely and tailor from the resume + job details.

    Returns:
        A configured :class:`crewai.Crew` ready for ``kickoff(inputs=...)``.
        The task list order is stable: the interview task is always last and the
        resume task always second-to-last (``crew.tasks[-1]`` / ``[-2]``).
    """
    tasks = build_tasks(include_github=include_github)

    agents = [profiler, resume_strategist, interview_preparer]
    if include_github:
        agents.insert(0, github_project_summarizer)

    return Crew(
        agents=agents,
        tasks=tasks,
        output_log_file=output_log_file,
        tracing=tracing,
        verbose=verbose,
    )
