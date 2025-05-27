# Job Application Agent

A powerful, multi-agent automation system for job applications, leveraging [Crew AI](https://www.crewai.com/) to orchestrate specialized agents that extract, analyze, and summarize job postings, tailor resumes, and prepare interview materials. The system integrates with Google BigQuery and Vertex AI for advanced document storage and semantic search, and features custom tools for LinkedIn and GitHub data extraction.

---

## 🚀 Features

- **Multi-Agent Orchestration:** Uses Crew AI to coordinate agents for research, profiling, resume tailoring, and interview preparation.
- **Custom Tools:** Includes LinkedIn job extractor and GitHub repo summarizer, built using Crew AI's extensible tool system.
- **BigQuery + Vertex AI:** Stores and semantically searches GitHub project data using Google BigQuery as a vector store and Vertex AI for embeddings.
- **Automated Resume Tailoring:** Aligns your resume with job requirements and optimizes for ATS.
- **Interview Prep:** Generates tailored interview questions and talking points.

---

## 🧑‍💻 Agents & Tools

### Crew AI Multi-Agent System

- **Crew AI** ([Homepage](https://www.crewai.com/)): The backbone of the system, enabling modular, collaborative agent workflows.
- **Agents:**
  - **Researcher:** Extracts and analyzes job requirements from LinkedIn.
  - **GitHub Project Summarizer:** Summarizes your most relevant GitHub projects.
  - **Profiler:** Compiles a comprehensive personal/professional profile.
  - **Resume Strategist:** Tailors your resume for each job.
  - **Interview Preparer:** Prepares interview questions and talking points.

### Crew AI Tools Used

- **FileReadTool:** Reads and processes resume files.
- **ScrapeWebsiteTool:** Scrapes web content for job and company info.
- **MDXSearchTool:** Performs semantic search on resume content.
- **SerperDevTool:** (If enabled) For advanced web search.

### Custom Tools

- **LinkedIn Job Extractor:** Scrapes and parses job details from LinkedIn job postings.
- **GitHub Repos Extractor:** Recursively fetches and summarizes public GitHub repositories for a user.
- **BigQuery/Vertex AI Integration:** Custom logic to store and search GitHub repo content using Google's cloud AI stack.

---

## 🏗️ Architecture

- **Python** (see `requirements.txt` for dependencies)
- **Crew AI** for agent orchestration
- **Google BigQuery** as a vector store for document embeddings
- **Vertex AI** for generating and querying semantic embeddings
- **BeautifulSoup, Requests** for web scraping
- **LangChain** for document loading and chunking

---

## ⚙️ Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/arijitde92/Job_Application_Agent.git
cd Job_Application_Agent
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# GitHub API token (for higher rate limits/private repos)
GITHUB_PERSONAL_ACCESS_TOKEN=your_github_token

# Google Cloud Project details
GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/gcp-service-account.json
GCP_PROJECT_ID=inbound-byway-457408-c9
GCP_DATASET_NAME=job_applier_app
GCP_LOCATION=asia-south2
GCP_TABLE_NAME=github_repo_data

# (Optional) Vertex AI Model
VERTEX_AI_MODEL=text-embedding-005
```

**Note:**  
- `GOOGLE_APPLICATION_CREDENTIALS` should point to your GCP service account JSON file with BigQuery and Vertex AI permissions.
- The default project, dataset, location, and table names are set in the code but can be overridden via environment variables.

### 4. Run the Application

```bash
python Job_Applier.py
```

---

## 🔑 API Keys & Cloud Setup

- **GitHub:** [Create a Personal Access Token](https://github.com/settings/tokens) and set `GITHUB_PERSONAL_ACCESS_TOKEN` in your `.env` file.
- **Google Cloud:**  
  - Enable BigQuery and Vertex AI APIs.
  - Create a service account with the necessary permissions.
  - Download the JSON key and set `GOOGLE_APPLICATION_CREDENTIALS` in your `.env` file.
- **OpenAI:**  
  - [Get an OpenAI API Key](https://platform.openai.com/account/api-keys)
  - Add `OPENAI_API_KEY=your_openai_api_key` to your `.env` file.
- **Serper:**  
  - [Get a Serper API Key](https://serper.dev/)
  - Add `SERPER_API_KEY=your_serper_api_key` to your `.env` file.

---

## 📚 How it Works

1. **Job Research:** The Researcher agent scrapes LinkedIn for job details.
2. **GitHub Analysis:** The GitHub Project Summarizer indexes and summarizes your public repos, storing embeddings in BigQuery using Vertex AI.
3. **Profile Compilation:** The Profiler agent creates a comprehensive profile using your resume, GitHub summaries, and job requirements.
4. **Resume Tailoring:** The Resume Strategist aligns your resume with the job description.
5. **Interview Prep:** The Interview Preparer generates custom interview questions and talking points.

---

## 🛠️ Extending the System

- Add new agents or tools by following the Crew AI documentation.
- Integrate additional data sources or cloud services as needed.

---

## 📄 License

MIT License

---

## 🤝 Acknowledgements

- [Crew AI](https://www.crewai.com/)
- [Google Cloud BigQuery](https://cloud.google.com/bigquery)
- [Vertex AI](https://cloud.google.com/vertex-ai)
- [LangChain](https://python.langchain.com/)
- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) 