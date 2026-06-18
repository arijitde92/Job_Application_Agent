from crewai import Crew
import os
import json
from dotenv import load_dotenv
from webpage_extractor import extract_linkedin_job_details, JobDetails
from logger import get_logger, log_token_usage

load_dotenv()
logger = get_logger(__name__)

from agents import (
    github_project_summarizer,
    profiler,
    resume_strategist,
    interview_preparer,
)

from tasks import (
    github_summary_task,
    profile_task,
    resume_strategy_task,
    interview_preparation_task,
)

# NOTE: researcher and research_task have been removed.
# Job details are extracted once here and injected into crew inputs as
# job_details_json so every task can access them directly.

job_applier_crew = Crew(
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
    output_log_file="crew_log.txt",
    tracing=True,
    verbose=True,
)

if __name__ == "__main__":
    job_posting_url = 'https://www.linkedin.com/jobs/view/4413867953/'

    # --- Single MCP scrape: extract job details once, reuse everywhere ---
    logger.info("Job_Applier.py: Extracting job details from: %s", job_posting_url)
    job_details: JobDetails = extract_linkedin_job_details(job_posting_url)
    logger.info(
        "Job_Applier.py: Extracted job '%s' at '%s'",
        job_details.job_name, job_details.company_name,
    )

    # Build the JSON passed to all crew tasks — exclude about_company (not needed)
    job_info = job_details.to_dict()
    job_info.pop("about_company", None)
    job_details_json = json.dumps(job_info, indent=2)

    applicant_name = 'Arijit De'
    github_url = 'https://github.com/arijitde92'
    resume_path = 'Arijit_De_Resume.md'

    job_application_inputs = {
        'applicant_name': applicant_name.replace(' ', '_'),
        'job_posting_url': job_posting_url,
        'job_name': job_details.job_name,
        'company_name': job_details.company_name,
        'github_url': github_url,
        'resume_path': resume_path,
        'job_details_json': job_details_json,   # injected into every task description
    }

    logger.info("Job_Applier.py: Job application inputs prepared for '%s' at '%s'",
                job_details.job_name, job_details.company_name)
    logger.info("Job_Applier.py: Starting job applier crew execution...")
    result = job_applier_crew.kickoff(inputs=job_application_inputs)
    logger.info("Job_Applier.py: Crew execution completed.")
    if hasattr(result, 'token_usage'):
        log_token_usage(result.token_usage, logger)