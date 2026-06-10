# Job Application Agent

A powerful, multi-agent automation system for job applications, leveraging [Crew AI](https://www.crewai.com/) to orchestrate specialized agents that extract, analyze, and summarize job postings, tailor resumes, and prepare interview materials. The system integrates with Google BigQuery and Vertex AI for advanced document storage and semantic search, and features custom tools for LinkedIn and GitHub data extraction.

This project also includes a **Full-Stack Web App version** with a **FastAPI backend** (using Google Cloud SQL MySQL and GCS storage) and a **React + Vite frontend** that displays live agent execution progress via Server-Sent Events (SSE).

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

This is always the first step. Run:

```bash
git clone https://github.com/arijitde92/Job_Application_Agent.git
cd Job_Application_Agent
```

---

### Option A: Running the Standalone CLI Agent

#### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

#### 2. Configure Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# GitHub API token
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

#### 3. Run the CLI Application

```bash
python Job_Applier.py
```

---

### Option B: Running the Full-Stack Web App

#### 1. Setup Python Environment & Dependencies

Create and activate a conda environment, then install the backend dependencies:

```bash
conda create -n job_agent python=3.12 -y
conda activate job_agent
pip install -r requirements.txt
pip install -r backend/requirements.txt
```

#### 2. Install Node.js

The frontend React application requires Node.js (v18+). If you do not have it installed, please download and install it from the official [Node.js Download Page](https://nodejs.org/en/download).

#### 3. Setup Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

#### 4. Configure Web App Environment Variables

Add the following database and storage configurations to the bottom of your `.env` file:

```env
# Cloud SQL MySQL (Direct Connection)
MYSQL_HOST=34.131.150.209
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=JobAgent@2026
MYSQL_DATABASE=job_applier

# Google Cloud Storage (GCS) for Resumes
GCS_BUCKET_NAME=job-applier-project-69718baa-b6cc-44ec-9fb-resumes

# JWT Authentication
# Generate using: python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET_KEY=drUVPzflrCADfaC7HjrydJYPPS_IpspPlXxjOELdfjI
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=1440

# Google OAuth (Optional — for Google Login)
GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
```

#### 5. Run the Web Application

Start the backend API server and frontend development server in separate terminals:

**Terminal 1 (Backend API):**

```bash
conda activate job_agent
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 (Frontend React):**

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in your browser.

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
