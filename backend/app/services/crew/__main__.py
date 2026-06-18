"""
Standalone CLI for the crew pipeline.

Run from the ``backend/`` directory:

    uv run python -m app.services.crew --url <linkedin-job-url> \\
        --github https://github.com/<user> --resume path/to/resume.md \\
        --name "Your Name"
"""

import argparse
import json

from dotenv import load_dotenv

from app.core.logging import get_logger, log_token_usage
from app.services.crew.crew import build_crew
from app.services.extractors.linkedin_extractor import (
    extract_linkedin_job_details,
    JobDetails,
)

load_dotenv()
logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the job-application crew end to end.")
    parser.add_argument("--url", default="https://www.linkedin.com/jobs/view/4413867953/",
                        help="LinkedIn job posting URL")
    parser.add_argument("--github", default="https://github.com/arijitde92",
                        help="GitHub profile URL")
    parser.add_argument("--resume", default="sample_data/Arijit_De_Resume.md",
                        help="Path to the applicant's resume (.md)")
    parser.add_argument("--name", default="Arijit De", help="Applicant name")
    args = parser.parse_args()

    # --- Single MCP scrape: extract job details once, reuse everywhere ---
    logger.info("crew CLI: Extracting job details from: %s", args.url)
    job_details: JobDetails = extract_linkedin_job_details(args.url)
    logger.info("crew CLI: Extracted job '%s' at '%s'",
                job_details.job_name, job_details.company_name)

    # Build the JSON passed to all crew tasks — exclude about_company (not needed)
    job_info = job_details.to_dict()
    job_info.pop("about_company", None)
    job_details_json = json.dumps(job_info, indent=2)

    job_application_inputs = {
        "applicant_name": args.name.replace(" ", "_"),
        "job_posting_url": args.url,
        "job_name": job_details.job_name,
        "company_name": job_details.company_name,
        "github_url": args.github,
        "resume_path": args.resume,
        "job_details_json": job_details_json,  # injected into every task description
    }

    logger.info("crew CLI: Starting job applier crew execution...")
    crew = build_crew()
    result = crew.kickoff(inputs=job_application_inputs)
    logger.info("crew CLI: Crew execution completed.")
    if hasattr(result, "token_usage"):
        log_token_usage(result.token_usage, logger)


if __name__ == "__main__":
    main()
