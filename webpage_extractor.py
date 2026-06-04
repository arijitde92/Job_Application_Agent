import requests
from bs4 import BeautifulSoup
import json
from logger import get_logger

logger = get_logger(__name__)

def extract_linkedin_job_details(url, json_output=False):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    logger.info("webpage_extractor.py: Fetching LinkedIn job details from URL: %s", url)
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        logger.error("webpage_extractor.py: Failed to fetch LinkedIn job details. Status code: %s", response.status_code)
        return {"error": "Failed to fetch page"}
    logger.info("webpage_extractor.py: Successfully fetched and parsed LinkedIn job details.")

    soup = BeautifulSoup(response.text, "html.parser")

    # Section 1: Company details and job title
    top_card = soup.find("section", class_="top-card-layout container-lined overflow-hidden babybear:rounded-[0px]")
    company_name = job_name = remote_opportunity = about_company = "N/A"
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
        remote_tag = top_card.find(string=lambda t: "remote" in t.lower())
        remote_opportunity = "Yes" if remote_tag else "No"
        # About Company
        about_tag = top_card.find("div", class_="topcard__org-info-container")
        about_company = about_tag.get_text(strip=True) if about_tag else "N/A"

    # Section 2: Job description
    desc_section = soup.find("section", class_="core-section-container my-3 description")
    job_description = responsibilities = requirements = "N/A"
    if desc_section:
        desc_text = desc_section.get_text(separator="\n", strip=True)
        job_description = desc_text

        # Try to split responsibilities and requirements heuristically
        lines = desc_text.splitlines()
        resp_idx = req_idx = None
        for i, line in enumerate(lines):
            if "responsibilit" in line.lower():
                resp_idx = i
            if "requirement" in line.lower() or "qualification" in line.lower():
                req_idx = i
        if resp_idx is not None and req_idx is not None:
            responsibilities = "\n".join(lines[resp_idx+1:req_idx]).strip() or "N/A"
            requirements = "\n".join(lines[req_idx+1:]).strip() or "N/A"
        elif resp_idx is not None:
            responsibilities = "\n".join(lines[resp_idx+1:]).strip() or "N/A"
        elif req_idx is not None:
            requirements = "\n".join(lines[req_idx+1:]).strip() or "N/A"

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
        "Remote work opportunity": remote_opportunity,
        "About Company": about_company,
        "Job description (What you will be doing)": job_description,
        "Responsibilities": responsibilities,
        "Requirements/Qualifications": requirements,
        "Seniority Level": seniority,
        "Employment type": emp_type,
        "Job function": job_func,
        "Industry": industry
    }
    if json_output:
        return result
    return json.dumps(result, indent=2)

if __name__ == "__main__":
    job_posting_url = "https://www.linkedin.com/jobs/view/4414651966/"
    job_details = extract_linkedin_job_details(job_posting_url)
    logger.info("webpage_extractor.py: Job details: %s", job_details)
