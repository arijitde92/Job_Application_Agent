from time import sleep
from typing import List, Union
from crewai import Agent, LLM
from crewai_tools import (
  FileReadTool,
  ScrapeWebsiteTool,
  MDXSearchTool,
  SerperDevTool
)
from crewai.tools import tool
import requests
from bs4 import BeautifulSoup
import json
import os
from dotenv import load_dotenv
from github_repo_extractor import process_github_repo_to_bq, query_github_vector_store
from webpage_extractor import extract_linkedin_job_details, JobDetails
from logger import get_logger
load_dotenv()
logger = get_logger(__name__)

# LLM configuration — Claude Sonnet 4
gemini_llm = LLM(
    model="gemini/gemini-2.5-flash",
    api_key=os.environ.get("GEMINI_API_KEY"),
    temperature=0.7,
    max_tokens=8000
)

search_tool = SerperDevTool()
scrape_tool = ScrapeWebsiteTool()
read_resume = FileReadTool()
semantic_search_resume = MDXSearchTool()

GITHUB_REPO_SEARCH_LIMIT = 10   # Number of repositories to scan and index

@tool("github_repos_extractor")
def extract_github_repos_tool(user_url: str) -> Union[List[str] | None]:
    """
    Extracts public GitHub repositories of a user.
    This function takes a GitHub user URL as input and retrieves
    the list of public repository URLs owned by that user.
    Args:
        user_url (str): The GitHub user URL.
    Returns:
        List[str]: A list of URLs of the user's public repositories.
    """

    def get_repository_links(user_url: str, github_repo_urls: List[str]) -> List[str]:
        """
        Recursively fetches all repository links from a GitHub user page.
        Args:
            user_url (str): The URL of the GitHub user page.
            github_repo_urls (List[str]): Accumulator for repo URLs.
        Returns:
            List[str]: A list of repository URLs.
        """
        if user_url[-1] == '/':
            user_url = user_url[:-1]

        user_url = user_url + "?tab=repositories"
        # Extract the username from the URL
        user_name = user_url.split('/')[-1].split('?')[0]

        # Fetch the url of each repository
        logger.info("agents.py: Searching URL: %s", user_url)
        response = requests.get(user_url, headers={'User-Agent': "Chrome/51.0.2704.106"})
        if response.status_code != 200:
            logger.error("agents.py: Error Occurred: Response Code: %s", response.status_code)
            return github_repo_urls
        html_content = response.content
        soup = BeautifulSoup(html_content, 'html.parser')
        repo_headings = soup.select('h3.wb-break-all')
        for repo_heading in repo_headings:
            repo_name = repo_heading.a.attrs["href"].split('/')[-1]
            link = 'https://github.com/' + user_name + "/" + repo_name
            logger.info("agents.py: Found repo: %s", link)
            github_repo_urls.append(link)
        pages = soup.find_all(attrs={"class": "next_page"})
        if len(pages) > 0:
            # find the next page link if <a href> exists
            if pages[0].name == 'a':
                next_page_link = 'https://github.com' + pages[0].attrs['href']
                github_repo_urls = get_repository_links(next_page_link, github_repo_urls)
        return github_repo_urls
    
    # Call the function to get repository links
    github_repo_urls = get_repository_links(user_url, [])
    logger.info("agents.py: Found %d repositories from %s", len(github_repo_urls), user_url)
    if not github_repo_urls:
        logger.warning("agents.py: No repositories found or an error occurred.")
        return None
    logger.info("agents.py: Found %d repositories for user %s", len(github_repo_urls), user_url)
    for repo_url in github_repo_urls[:GITHUB_REPO_SEARCH_LIMIT]:
        process_github_repo_to_bq(repo_url,
                                  file_filter=lambda file_path: file_path.endswith(('.py', '.ipynb', '.md', '.txt')),
                                  access_token=os.environ.get('GITHUB_PERSONAL_ACCESS_TOKEN'))
        sleep(20)
    
    return github_repo_urls[:GITHUB_REPO_SEARCH_LIMIT]  # Limit to first GITHUB_REPO_SEARCH_LIMIT repositories


@tool("linkedin_job_extractor")
def extract_linkedin_job_details_tool(url: str) -> str:
    """
    Extracts job details from a LinkedIn job posting page using Bright Data MCP.
    Scrapes the page and returns a JSON-formatted string containing structured
    job information including company name, job title, location, seniority level,
    employment type, job function, industry, job description, and requirements.

    Args:
        url (str): The URL of the LinkedIn job posting.

    Returns:
        str: A JSON-formatted string containing the extracted job details.
             Returns an error message JSON if scraping fails.
    """
    logger.info("agents.py: extract_linkedin_job_details_tool called for URL: %s", url)
    job: JobDetails = extract_linkedin_job_details(url)
    logger.info(
        "agents.py: Extracted job '%s' at '%s'",
        job.job_name, job.company_name
    )
    return job.to_agent_string()

@tool("repo_content_searcher")
def repo_content_searcher(query: str, job_description: str = None, top_k: int = 5):
    """
    Searches the vector store for relevant GitHub repo content using the query, resume, and job description as context.
    Args:
        query (str): The search query.
        resume (str): The user's resume (optional, used as context).
        job_description (str): The job description (optional, used as context).
        top_k (int): Number of top results to return.
    Returns:
        List[dict]: List of relevant content chunks with metadata.
    """
    # Combine context for a richer query
    context = ""
    if job_description:
        context += f"Job Description: {job_description}\n"
    full_query = f"{context}\nQuery: {query}"
    results = query_github_vector_store(full_query, top_k=top_k)
    return [
        {
            "content": content,
            "metadata": metadata
        } for content, metadata in results
    ]

# Agent 1: GitHub Project Summarizer
# NOTE: The Researcher agent has been removed. Job extraction is done
# directly in Job_Applier.py via extract_linkedin_job_details() and the
# structured JobDetails are passed into every task via crew input variables.
github_project_summarizer = Agent(
    role="GitHub Project Summarizer",
    goal="Summarize the user's most relevant GitHub projects for a job application, highlighting tech stacks, languages, frameworks, tools, and cloud technologies used.",
    tools=[extract_github_repos_tool, repo_content_searcher],
    llm=gemini_llm,
    verbose=True,
    backstory=(
        "You are an expert in analyzing GitHub repositories and summarizing project experience for job applications. "
        "You first ensure the user's repositories are indexed in the vector store using the github_repos_extractor tool. "
        "Then, you use the repo_content_searcher tool to find the most relevant project content based on the job description and resume. "
        "Finally, you create a concise summary of the user's relevant GitHub projects, highlighting the tech stacks, programming languages, frameworks, tools, and any cloud computing technologies used."
    )
)


# Agent 3: Profiler
profiler = Agent(
    role="Personal Profiler for Engineers",
    goal="Do incredible analytical research on job applicants to help them stand out in the job market",
    tools = [read_resume, semantic_search_resume],
    llm=gemini_llm,
    verbose=True,
    backstory=(
        "Equipped with analytical prowess, you dissect and synthesize information "
        "from diverse sources to craft comprehensive personal and professional profiles,"
        "laying the groundwork for personalized resume enhancements."
    )
)

# Agent 4: Resume Strategist
resume_strategist = Agent(
    role="Resume Strategist for Engineers",
    goal="Find all the best ways to make a resume stand out in the job market.",
    tools = [scrape_tool, search_tool,
             read_resume, semantic_search_resume],
    llm=gemini_llm,
    verbose=True,
    backstory=(
        "With a strategic mind and an eye for detail, you "
        "excel at refining resumes to highlight the most "
        "relevant skills and experiences, ensuring they "
        "resonate perfectly with the job's requirements."
    )
)

# Agent 5: Interview Preparer
interview_preparer = Agent(
    role="Engineering Interview Preparer",
    goal="Create interview questions and talking points "
         "based on the resume and job requirements",
    tools = [scrape_tool, search_tool,
             read_resume, semantic_search_resume],
    llm=gemini_llm,
    verbose=True,
    backstory=(
        "Your role is crucial in anticipating the dynamics of "
        "interviews. With your ability to formulate key questions "
        "and talking points, you prepare candidates for success, "
        "ensuring they can confidently address all aspects of the "
        "job they are applying for."
    )
)

if __name__ == "__main__":
    logger.info("agents.py: Testing agents.py")
    # print("Testing LinkedIn Job Details Extraction Tool")
    # job_posting_url = "https://www.linkedin.com/jobs/view/4234610887/"
    # job_details = extract_linkedin_job_details_tool.run(url=job_posting_url)
    # print(job_details)

    # print("\nTesting GitHub Repositories Extraction Tool")
    # github_user_url = "https://github.com/arijitde92?tab=repositories"
    # github_repos = extract_github_repos_tool.run(user_url=github_user_url)
    # print(github_repo for github_repo in github_repos[:5])  # Print first 5 for verification
    # print("\nTesting GitHub Repo Search Tool")
    # for repo in github_repos[:3]:
    #     github_repo_search.add(repo=repo, content_types=['code'])
    #     github_repo_search_result = github_repo_search.run(repo)
    #     print(f"Repo: {repo}\nSearch Result: {github_repo_search_result}\n")
