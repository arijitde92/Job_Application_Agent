"""Shared sample resume content for the LaTeX tests."""

import copy

SAMPLE_RESUME = {
    "personal_information": {
        "name": "Ada Lovelace",
        "location": "London, UK",
        "email": "ada@example.com",
        "phone": "+44 20 1234 5678",
        "linkedin": "linkedin.com/in/ada",
        "github": "https://github.com/ada",
    },
    "summary": "Senior AI Engineer with **6+ years** building agentic systems.",
    "experience": [
        {
            "job_title": "Senior AI Engineer",
            "company": "Acme",
            "location": "Singapore",
            "work_mode": "Remote, Full time",
            "start_date": "SEP 2025",
            "end_date": "PRESENT",
            "bullets": [
                "Built **LangGraph** agents, cutting token usage by 35%.",
                "Designed a RAG pipeline over 10M+ vectors.",
            ],
        },
        {
            "job_title": "ML Engineer",
            "company": "Analytical Engines Ltd",
            "location": "London",
            "work_mode": "Onsite",
            "start_date": "JAN 2020",
            "end_date": "AUG 2025",
            "bullets": ["Trained 3D segmentation models in **PyTorch**."],
        },
    ],
    "education": [
        {
            "degree": "M.E. Computer Science & Engineering",
            "institution": "Jadavpur University",
            "location": "India",
            "gpa": "8.83",
            "start_date": "AUG 2016",
            "end_date": "JUN 2018",
        }
    ],
    "skills": [
        {"category": "Languages", "skills": ["Python", "C++", "SQL"]},
        {"category": "Cloud & Ops", "skills": ["GCP", "Docker"]},
    ],
    "projects": [
        {
            "name": "Civil RAG",
            "url": "https://github.com/ada/Civil_RAG",
            "bullets": ["Multilingual RAG with citations."],
        }
    ],
    "publications": [{"citation": "A. Lovelace, Notes on the Analytical Engine, 1843."}],
    "certifications": [
        {"name": "Google AI Professional Certificate", "issuer": "Google", "year": "2026",
         "url": "https://coursera.org/share/abc"}
    ],
}


def sample_resume() -> dict:
    """A fresh deep copy of the sample resume."""
    return copy.deepcopy(SAMPLE_RESUME)
