from crewai import Task
from agents import *

# Task for Researcher Agent: Extract Job Requirements
research_task = Task(
    description=(
        "Analyze the job posting details by using the linkedin job extractor tool passing the URL {job_posting_url} as input. "
        "Extract meaningful information about the job requirements from the json formatted string that includes "
        "company name, job name, remote work opportunity, about company, job description, responsibilities, "
        "requirements/qualifications, seniority level, employment type, job function, and industry. "
        "Make sure to extract the job requirements and qualifications in a structured format."
    ),
    expected_output=(
        """The job details in the following in JSON Format-
            Company Name -
            Job Name -
            Remote work oppurtunity - Yes/No
            About Company -
            Job description (What you will be doing)
            Responsibilities -
            Requirements/Qualifications -
            Seniority Level -
            Employment type -
            Job function -
            Industry -

            If you cannot find any of the above information, please return "N/A" for that field.
        """
    ),
    tools=[extract_linkedin_job_details_tool],
    agent=researcher,
    async_execution=True
)

# Task for GitHub Project Summarizer Agent: Summarize relevant GitHub projects
github_summary_task = Task(
    description=(
        "Use the extract_github_repos_tool with user's github url: {github_url} as input to store the repository contents in the database."
        "Find the tech stacks, technologies and skills required from the job description acquired from the research task and then formulate a query asking for these things. "
        "Then use the repo_content_searcher tool with input argument 'query' as the query you formulated earlier and also pass the job description for input argument 'job_description'. "
        "Using the information about the relevant GitHub projects returned from the tool, "
        "analyze the job requirements from the research_task and identify and summarize the user's most relevant GitHub projects that match the job description. "
        "Highlight the tech stacks, programming languages, frameworks, tools, and cloud computing technologies used in these projects. "
        "The summary should be concise and focused on helping a recruiter or hiring manager quickly understand the user's relevant project experience. "
    ),
    context=[research_task],
    expected_output=(
        "A concise summary of the user's most relevant GitHub projects, including: "
        "project names, tech stacks, programming languages, frameworks, tools, and cloud technologies used. "
        "The summary should clearly connect the user's project experience to the job requirements."
    ),
    tools=[extract_github_repos_tool, repo_content_searcher],
    agent=github_project_summarizer,
    async_execution=False
)


# Task for Profiler Agent: Compile Comprehensive Profile
profile_task = Task(
    description=(
        "Compile a detailed personal and professional profile "
        "Read and semantically understand the resume contents from {resume_path}. "
        "Using the job requirements received from researcher agent from previous tasks, GitHub projects summary received from github_project_summarizer agent in the previous task, personal write-up ({personal_writeup}) and the resume contents, "
        "create a comprehensive profile."
    ),
    context=[research_task, github_summary_task],
    expected_output=(
        "A comprehensive profile document that includes skills, "
        "project experiences, contributions, interests, and "
        "communication style."
    ),
    tools=[read_resume, semantic_search_resume],
    agent=profiler,
    async_execution=False
)

# Task for Resume Strategist Agent: Align Resume with Job Requirements
resume_strategy_task = Task(
    description=(
        "Using the profile, github projects summary and job requirements obtained from previous tasks, tailor the resume to highlight the most "
        "relevant areas. Employ tools to adjust and enhance the resume content. "
        "Make sure this is the best resume that cathches the eye of the recruiter. "
        "Update every section, inlcuding the initial summary, work experience, skills, and education. "
        "Make sure you include relevant keywords from the job description to ensure the resume is optimized for ATS (Applicant Tracking Systems). You may use fictitious but practical, believable numbers to quantify some achievements or skills described in work history or projects."
    ),
    expected_output=(
        "An updated resume that effectively highlights the candidate's qualifications and experiences relevant to the job."
    ),
    output_file="{applicant_name}_{company_name}_{job_name}_resume.md",
    context=[research_task, profile_task],
    agent=resume_strategist,
    tools=[read_resume, semantic_search_resume],
    markdown=True,
    async_execution=False
)

# Task for Interview Preparer Agent: Develop Interview Materials
interview_preparation_task = Task(
    description=(
        "Create a set of potential interview questions and talking points based on the tailored resume and job requirements. "
        "Utilize tools to generate relevant questions and discussion points. Make sure to use these question and talking points to "
        "help the candiadte highlight the main points of the resume and how it matches the job posting."
    ),
    expected_output=(
        "A document containing key questions and talking points that the candidate should prepare for the initial interview."
    ),
    output_file="interview_materials.md",
    context=[research_task, profile_task, resume_strategy_task],
    agent=interview_preparer,
    async_execution=False
)


