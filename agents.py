from time import sleep
from typing import List, Union
from crewai import Agent
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
load_dotenv()

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
        print("Searching URL: ", user_url)
        response = requests.get(user_url, headers={'User-Agent': "Chrome/51.0.2704.106"})
        if response.status_code != 200:
            print("Error Occurred: Response Code: ", response.status_code)
            return github_repo_urls
        html_content = response.content
        soup = BeautifulSoup(html_content, 'html.parser')
        repo_headings = soup.select('h3.wb-break-all')
        for repo_heading in repo_headings:
            repo_name = repo_heading.a.attrs["href"].split('/')[-1]
            link = 'https://github.com/' + user_name + "/" + repo_name
            print("Found repo:", link)
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
    print(f"Found {len(github_repo_urls)} repositories from {user_url}")
    if not github_repo_urls:
        print("No repositories found or an error occurred.")
        return None
    print(f"Found {len(github_repo_urls)} repositories for user {user_url}")
    for repo_url in github_repo_urls[:GITHUB_REPO_SEARCH_LIMIT]:
        process_github_repo_to_bq(repo_url,
                                  file_filter=lambda file_path: file_path.endswith(('.py', '.ipynb', '.md', '.txt')),
                                  access_token=os.environ.get('GITHUB_PERSONAL_ACCESS_TOKENB_TOKEN'))
        sleep(20)
    
    return github_repo_urls[:GITHUB_REPO_SEARCH_LIMIT]  # Limit to first GITHUB_REPO_SEARCH_LIMIT repositories


@tool("linkedin_job_extractor")
def extract_linkedin_job_details_tool(url: str):
    """
    Extracts job details from a LinkedIn job posting page.
    This function takes a LinkedIn job posting URL as input and scrapes the page to extract
    various details about the job, such as the company name, job title, seniority level,
    employment type, job function, industry, and job description. The extracted details
    are returned as a JSON-formatted string.
    Args:
        url (str): The URL of the LinkedIn job posting.
    Returns:
        str: A JSON-formatted string containing the extracted job details. If the page
        cannot be fetched, an error message is returned in the JSON.
    
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {"error": "Failed to fetch page"}

    soup = BeautifulSoup(response.text, "html.parser")

    # Section 1: Company details and job title
    top_card = soup.find("section", class_="top-card-layout container-lined overflow-hidden babybear:rounded-[0px]")
    company_name = job_name = "N/A"
    if top_card:
        # Job Name
        job_title_tag = top_card.find(["h1", "h2"])
        job_name = job_title_tag.get_text(strip=True) if job_title_tag else "N/A"
        # Company Name
        company_tag = top_card.find("a", class_="topcard__org-name-link")
        if not company_tag:
            company_tag = top_card.find("span", class_="topcard__flavor")
        company_name = company_tag.get_text(strip=True) if company_tag else "N/A"
        # Remote Opportunity
        # remote_tag = top_card.find(string=lambda t: "remote" in t.lower())
        # remote_opportunity = "Yes" if remote_tag else "No"
        # About Company
        # about_tag = top_card.find("div", class_="topcard__org-info-container")
        # about_company = about_tag.get_text(strip=True) if about_tag else "N/A"

    # Section 2: Job description
    desc_section = soup.find("section", class_="core-section-container my-3 description")
    # job_description = responsibilities = requirements = "N/A"
    # if desc_section:
    #     desc_text = desc_section.get_text(separator="\n", strip=True)
    #     job_description = desc_text

    #     # Try to split responsibilities and requirements heuristically
    #     lines = desc_text.splitlines()
    #     resp_idx = req_idx = None
    #     for i, line in enumerate(lines):
    #         if "responsibilit" in line.lower():
    #             resp_idx = i
    #         if "requirement" in line.lower() or "qualification" in line.lower():
    #             req_idx = i
    #     if resp_idx is not None and req_idx is not None:
    #         responsibilities = "\n".join(lines[resp_idx+1:req_idx]).strip() or "N/A"
    #         requirements = "\n".join(lines[req_idx+1:]).strip() or "N/A"
    #     elif resp_idx is not None:
    #         responsibilities = "\n".join(lines[resp_idx+1:]).strip() or "N/A"
    #     elif req_idx is not None:
    #         requirements = "\n".join(lines[req_idx+1:]).strip() or "N/A"

    # Sidebar details (Seniority, Employment type, etc.)
    sidebar = soup.find("ul", class_="description__job-criteria-list")
    seniority = emp_type = job_func = industry = "N/A"
    if sidebar:
        for li in sidebar.find_all("li"):
            text = li.get_text(strip=True)
            if "Seniority level" in text:
                seniority = li.find("span", class_="description__job-criteria-text").get_text(strip=True)
            elif "Employment type" in text:
                emp_type = li.find("span", class_="description__job-criteria-text").get_text(strip=True)
            elif "Job function" in text:
                job_func = li.find("span", class_="description__job-criteria-text").get_text(strip=True)
            elif "Industries" in text:
                industry = li.find("span", class_="description__job-criteria-text").get_text(strip=True)

    result = {
        "Company Name": company_name,
        "Job Name": job_name,
        "Seniority Level": seniority,
        "Employment type": emp_type,
        "Job function": job_func,
        "Industry": industry,
        "Job Description": desc_section.get_text(separator="\n", strip=True) if desc_section else "N/A"
    }
    return json.dumps(result, indent=2)

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

# Agent 1: Researcher
researcher = Agent(
    role="Tech Job Researcher",
    goal="Make sure to do careful and detailed analysis on job posting to help job applicants",
    tools = [extract_linkedin_job_details_tool],
    verbose=True,
    backstory=(
        "As a Job Researcher, your prowess in "
        "navigating and extracting critical "
        "information from job postings is unmatched."
        "Your skills help pinpoint the necessary "
        "qualifications and skills sought "
        "by employers, forming the foundation for "
        "effective application tailoring."
    )
)

# Agent 2: GitHub Project Summarizer
github_project_summarizer = Agent(
    role="GitHub Project Summarizer",
    goal="Summarize the user's most relevant GitHub projects for a job application, highlighting tech stacks, languages, frameworks, tools, and cloud technologies used.",
    tools=[extract_github_repos_tool, repo_content_searcher],
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
    print("Testing agents.py")
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
