from crewai import Crew
import os
from dotenv import load_dotenv
from webpage_extractor import extract_linkedin_job_details
load_dotenv()

from agents import (
    researcher,
    github_project_summarizer,
    profiler,
    resume_strategist,
    interview_preparer
)

from tasks import (
    research_task,
    github_summary_task,
    profile_task,
    resume_strategy_task,
    interview_preparation_task
)

job_applier_crew = Crew(
    agents=[researcher,
            github_project_summarizer,
            profiler,
            resume_strategist,
            interview_preparer],

    tasks=[research_task,
           github_summary_task,
           profile_task,
           resume_strategy_task,
           interview_preparation_task],
    output_log_file="crew_log.txt"
    # verbose=True
)

if __name__ == "__main__":
    job_posting_url = 'https://www.linkedin.com/jobs/view/4414651966'
    job_details = extract_linkedin_job_details(job_posting_url, json_output=True)
    job_name = job_details['Job Name']
    company_name = job_details['Company Name']
    appicant_name = 'Arijit De'
    github_url = 'https://github.com/arijitde92'
    resume_path = 'Arijit_De_Resume.md'
    # personal_summary = """Arijit De is an AI and machine learning specialist with experience in deep learning, backend development, and cloud deployment.
    #     At mVizn Pte. Ltd., he enhances semantic segmentation models for 3D point clouds, improving performance and scalability.
    #     Previously, at Mercedes-Benz Research and Development India, he advanced ADAS capabilities by training YOLO v3 models for Vulnerable Road User detection.
    #     His expertise spans algorithm design, cloud security, and automation.
    #     Beyond his professional work, he has built AI-driven projects like an assignment submission portal with automated code evaluation,
    #     a spiritual chatbot using LLMs, a GitHub code analysis tool, and a 3D brain segmentation app, showcasing his technical versatility and innovation."""
    job_application_inputs = {
        'applicant_name': appicant_name.replace(' ', '_'),
        'job_posting_url': job_posting_url,
        'job_name': job_name,
        'company_name': company_name,
        'github_url': github_url,
        'resume_path': resume_path,
        # 'personal_writeup': personal_summary
    }
    print("Job application inputs")
    print(job_application_inputs)
    ### this execution will take a few minutes to run
    result = job_applier_crew.kickoff(inputs=job_application_inputs)