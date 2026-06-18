# Job Application Agent

A powerful, multi-agent automation system for job applications, leveraging [Crew AI](https://www.crewai.com/) to orchestrate specialized agents that extract, analyze, and summarize job postings, tailor resumes, and prepare interview materials. The system integrates with Google BigQuery and Vertex AI for advanced document storage and semantic search, and features custom tools for LinkedIn and GitHub data extraction.

This project also includes a **Full-Stack Web App version** with a **FastAPI backend** (using Google Cloud SQL MySQL and GCS storage) and a **React + Vite frontend** that displays live agent execution progress via Server-Sent Events (SSE).

---

## 📦 Project Structure

```
Job_Application_Agent/
├── backend/                # FastAPI backend + CrewAI pipeline (uv-managed)
│   ├── app/
│   │   ├── api/            # Route handlers (api/v1) + shared deps
│   │   ├── core/           # config, security, database, logging
│   │   ├── services/       # GCS, crew runner, CrewAI pipeline, extractors
│   │   ├── models/         # SQLAlchemy ORM models
│   │   ├── schemas/        # Pydantic schemas
│   │   └── main.py         # App init + CORS + routers
│   ├── credentials/        # GCP keys (gitignored)
│   ├── pyproject.toml      # Dependencies (uv)
│   ├── Dockerfile          # (empty for now)
│   └── README.md           # Backend setup & run instructions
├── frontend/               # React + Vite (JavaScript) frontend
│   ├── src/
│   │   ├── components/ pages/ context/ hooks/ services/ assets/
│   │   └── main.jsx App.jsx
│   ├── Dockerfile          # (empty for now)
│   └── package.json
├── .github/workflows/      # CI/CD (deploy.yml — empty for now)
├── docker-compose.yml      # (empty for now)
└── README.md               # This file
```

## ✅ Prerequisites

| Requirement | Version / Notes |
| ----------- | --------------- |
| **Python** | 3.11+ (3.12 recommended). You don't need to install it manually — `uv` will fetch a matching interpreter. |
| **[uv](https://docs.astral.sh/uv/)** | The Python package & project manager used here. Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` (then restart your shell so `~/.local/bin` is on `PATH`). |
| **Node.js** | 18+ (with `npm`), for the React frontend. [Download](https://nodejs.org/en/download). |
| **Google Cloud account** | A GCP project with **BigQuery**, **Vertex AI**, **Cloud SQL Admin**, and **Cloud Storage** APIs enabled, plus a **service-account JSON key**. See [backend/docs/GCP_Setup.md](backend/docs/GCP_Setup.md). |
| **Cloud SQL (MySQL)** | A Cloud SQL MySQL instance (the web app stores users/jobs there). |
| **[Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/mysql/sql-proxy)** | `cloud-sql-proxy` binary on `PATH` (the `uv run db-proxy` / `uv run dev` commands invoke it). See [Installation](https://docs.cloud.google.com/sql/docs/mysql/connect-instance-auth-proxy) to install|
| **API keys** | GitHub PAT, Bright Data, Gemini, and (optionally) Serper. See [API Keys & Cloud Setup](#-api-keys--cloud-setup). |

> **All `uv run` commands are run from the `backend/` directory** — the project
> and its custom commands are defined in `backend/pyproject.toml`. There is no
> root-level `pyproject.toml`, so `uv run dev` from the repo root will not work.

---

## ⚡ Quick Start

```bash
git clone https://github.com/arijitde92/Job_Application_Agent.git
cd Job_Application_Agent
```

### 1. Backend (uv)

```bash
cd backend
cp .env.example .env          # then fill in secrets (see below)
uv sync                       # creates backend/.venv and installs everything
```

Place your GCP service-account JSON key under `backend/credentials/` and point
`GOOGLE_APPLICATION_CREDENTIALS` in `backend/.env` at it (an absolute path is
safest).

### 2. Frontend

```bash
cd ../frontend
cp .env.example .env
npm install
```

### 3. Run everything (from `backend/`)

```bash
cd ../backend
uv run dev                    # Cloud SQL proxy + API + frontend, together
```

Then open **http://localhost:5173**. The API is at http://localhost:8000
(docs at http://localhost:8000/docs).

Prefer separate terminals? Run them individually:

```bash
uv run db-proxy   # Cloud SQL Auth Proxy → 127.0.0.1:3306
uv run api        # FastAPI dev server  → http://localhost:8000
uv run frontend   # Vite dev server     → http://localhost:5173
```

See **[backend/README.md](backend/README.md)** for the full command list,
env-var overrides, and the standalone crew CLI.

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

- **Python 3.11+** with **[uv](https://docs.astral.sh/uv/)** for dependency &
  environment management (see `backend/pyproject.toml`)
- **FastAPI** backend (async SQLAlchemy + Cloud SQL MySQL, JWT/Google OAuth auth)
- **React + Vite** frontend with live progress via Server-Sent Events (SSE)
- **Crew AI** for agent orchestration
- **Google BigQuery** as a vector store for document embeddings
- **Vertex AI** for generating and querying semantic embeddings
- **Google Cloud Storage** for resume & generated-output storage
- **Bright Data MCP** for LinkedIn job scraping; **LangChain** for document
  loading and chunking

---

## ⚙️ Configuration

All backend configuration lives in **`backend/.env`** (copy it from
`backend/.env.example`). Key groups:

```env
# ── GCP ──────────────────────────────────────────────────────────
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/backend/credentials/service-account.json
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=asia-south2
GCP_DATASET_NAME=job_applier_app
GCP_TABLE_NAME=github_repo_data

# ── API keys ─────────────────────────────────────────────────────
GITHUB_PERSONAL_ACCESS_TOKEN=...
BRIGHT_DATA_API_KEY=...          # LinkedIn scraping (Bright Data MCP)
GEMINI_API_KEY=...               # Crew LLM + Vertex embeddings
SERPER_API_KEY=...               # optional web search

# ── Cloud SQL (MySQL) ────────────────────────────────────────────
MYSQL_HOST=127.0.0.1             # 127.0.0.1 when using the Cloud SQL Auth Proxy
MYSQL_PORT=3306
MYSQL_USER=...
MYSQL_PASSWORD=...
MYSQL_DATABASE=job_applier

# ── GCS / Auth ───────────────────────────────────────────────────
GCS_BUCKET_NAME=your-resumes-bucket
JWT_SECRET_KEY=                  # generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
GOOGLE_OAUTH_CLIENT_ID=          # optional, for "Sign in with Google"
GOOGLE_OAUTH_CLIENT_SECRET=
```

The frontend has its own `frontend/.env` (copy from `frontend/.env.example`) for
`VITE_*` variables.

> **Never commit `.env` or service-account keys.** They are gitignored. The
> `db-proxy` connection name is derived from `GCP_PROJECT_ID`, `GCP_LOCATION`,
> and instance `job-applier-mysql` by default — override with `CLOUD_SQL_INSTANCE`
> if yours differs.

For step-by-step GCP provisioning (project, BigQuery dataset, Cloud SQL instance,
GCS bucket, service account), see **[backend/docs/GCP_Setup.md](backend/docs/GCP_Setup.md)**.

---

## 🖥️ Standalone Crew CLI (no web app)

You can run the agent pipeline directly, without the frontend/DB, from `backend/`:

```bash
cd backend
uv run crew \
    --url https://www.linkedin.com/jobs/view/<id>/ \
    --github https://github.com/<user> \
    --resume sample_data/Arijit_De_Resume.md \
    --name "Your Name"
```

This extracts the job, indexes the GitHub repos into BigQuery, tailors the
resume, and writes interview materials — same pipeline the web app drives.

---

## 🔑 API Keys & Cloud Setup

All keys go in `backend/.env`.

- **GitHub:** [Create a Personal Access Token](https://github.com/settings/tokens) → `GITHUB_PERSONAL_ACCESS_TOKEN`.
- **Google Cloud:**
  - Enable **BigQuery**, **Vertex AI**, **Cloud SQL Admin**, and **Cloud Storage** APIs.
  - Create a service account with the relevant roles (BigQuery, Vertex AI User, Cloud SQL Client, Storage Object Admin).
  - Download the JSON key into `backend/credentials/` and point `GOOGLE_APPLICATION_CREDENTIALS` at it.
- **Gemini:** [Get a Gemini API key](https://aistudio.google.com/app/apikey) → `GEMINI_API_KEY` (the crew's LLM + embeddings).
- **Bright Data:** [Get an API key](https://brightdata.com/) for the MCP scraper → `BRIGHT_DATA_API_KEY` (LinkedIn job extraction).
- **Serper (optional):** [Get a Serper API key](https://serper.dev/) → `SERPER_API_KEY` (web search tool).

---

## 📚 How it Works

1. **Job Extraction:** The job posting is scraped once via the Bright Data MCP LinkedIn extractor and parsed into structured `JobDetails`, which are injected into every downstream task.
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
