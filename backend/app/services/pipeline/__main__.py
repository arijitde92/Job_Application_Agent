"""
Standalone CLI for the LangGraph pipeline.

Run from the ``backend/`` directory:

    uv run pipeline --url <linkedin-job-url> \\
        --github https://github.com/<user> --resume path/to/resume.md \\
        --name "Your Name"

Writes ``<applicant>_<company>_<job>_resume.md`` and ``interview_materials.md``
to the current directory and logs token usage. Traces go to LangSmith when
``LANGSMITH_TRACING=true``. The CLI is Markdown-only: no GCS upload, no
database write, no .docx generation.
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.core.config import get_settings
from app.core.logging import get_logger, log_token_usage
from app.services.extractors.linkedin_extractor import (
    JobDetails,
    extract_linkedin_job_details,
)
from app.services.pipeline.graph import PipelineInputs, build_pipeline
from app.services.pipeline.tools import (
    GithubIndexContext,
    github_username_from_url,
)
from app.services.pipeline.tools.docx_tools import sanitize_filename_component
from app.services.pipeline.tracing import build_run_config

load_dotenv()
get_settings()  # exports LANGSMITH_* to os.environ
logger = get_logger(__name__)


def main() -> None:
    """Parse arguments, run the pipeline end to end, and write the outputs."""
    parser = argparse.ArgumentParser(description="Run the job-application pipeline end to end.")
    parser.add_argument("--url", default="https://www.linkedin.com/jobs/view/4413867953/",
                        help="LinkedIn job posting URL")
    parser.add_argument("--github", default="https://github.com/arijitde92",
                        help="GitHub profile URL (pass an empty string to skip GitHub)")
    parser.add_argument("--resume", default="sample_data/Arijit De Resume 2026.md",
                        help="Path to the applicant's resume (.md)")
    parser.add_argument("--name", default="Arijit De", help="Applicant name")
    args = parser.parse_args()

    # Extract job details once; every task prompt reuses them.
    logger.info("pipeline CLI: Extracting job details from: %s", args.url)
    job_details: JobDetails = extract_linkedin_job_details(args.url)
    logger.info("pipeline CLI: Extracted job '%s' at '%s'",
                job_details.job_name, job_details.company_name)

    job_info = job_details.to_dict()
    job_info.pop("about_company", None)
    applicant_name = args.name.replace(" ", "_")
    inputs = PipelineInputs(
        applicant_name=applicant_name,
        job_posting_url=args.url,
        job_name=job_details.job_name,
        company_name=job_details.company_name,
        job_details_json=json.dumps(job_info, indent=2),
    )

    include_github = bool(args.github)
    github_ctx = None
    if include_github:
        inputs["github_url"] = args.github
        github_ctx = GithubIndexContext(
            github_url=args.github,
            github_username=github_username_from_url(args.github),
        )

    graph = build_pipeline(
        include_github=include_github, resume_path=args.resume, github_ctx=github_ctx
    )
    usage = UsageMetadataCallbackHandler()
    config = build_run_config(
        include_github=include_github, callbacks=[usage], extra_tags=["cli"]
    )

    logger.info("pipeline CLI: Starting pipeline execution...")
    state = graph.invoke({"inputs": inputs}, config)
    logger.info("pipeline CLI: Pipeline execution completed.")

    resume_file = Path(
        f"{sanitize_filename_component(applicant_name)}_"
        f"{sanitize_filename_component(job_details.company_name)}_"
        f"{sanitize_filename_component(job_details.job_name)}_resume.md"
    )
    resume_file.write_text(state.get("tailored_resume_md", ""), encoding="utf-8")
    Path("interview_materials.md").write_text(
        state.get("interview_materials_md", ""), encoding="utf-8"
    )
    logger.info("pipeline CLI: Wrote %s and interview_materials.md", resume_file)
    log_token_usage(usage.usage_metadata, logger)


if __name__ == "__main__":
    main()
