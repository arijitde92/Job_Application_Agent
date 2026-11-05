"""
app.services.pipeline.schemas
-----------------------------
Structured-output schemas for the pipeline's agents.

``GithubReposSummary`` is the GitHub summarizer agent's ``response_format``
(LangChain ``ToolStrategy``): the model answers by calling a synthetic tool
whose arguments are validated against this schema, so downstream nodes always
receive a well-formed JSON summary.
"""

from pydantic import BaseModel, Field


class ChosenGithubRepo(BaseModel):
    """One GitHub repository picked as relevant to the target job."""

    github_repo_name: str = Field(
        description="The name of the selected GitHub repo."
    )
    relevant_skills: list[str] = Field(
        default_factory=list,
        description=(
            "The relevant skills used in this repo that match or are needed in "
            "the job description."
        ),
    )
    justification: str = Field(
        description=(
            "A justification of why this repo is chosen and how it is relevant "
            "for the given job description, explaining how the skills used in "
            "this repo are relevant for the job."
        ),
    )


class GithubReposSummary(BaseModel):
    """Structured output of the GitHub summary agent."""

    chosen_github_repos: list[ChosenGithubRepo] = Field(
        default_factory=list,
        description="The applicant's repos that are most relevant to the job.",
    )
