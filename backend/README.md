# Job Application Agent — Backend

FastAPI backend plus the CrewAI multi-agent pipeline that tailors resumes and
generates interview materials from a LinkedIn job posting and a GitHub profile.

## Layout

```
backend/
├── app/
│   ├── api/
│   │   ├── v1/                 # Versioned route handlers
│   │   │   ├── auth.py         #   /api/auth   — register, login, Google OAuth
│   │   │   ├── github.py       #   /api/github — GitHub profile CRUD
│   │   │   ├── resumes.py      #   /api/resumes — upload / preview / delete
│   │   │   └── jobs.py         #   /api/jobs   — tailor, status (SSE), downloads
│   │   └── deps.py             # Shared dependencies (get_db, get_current_user)
│   ├── core/
│   │   ├── config.py           # Pydantic BaseSettings (.env)
│   │   ├── security.py         # JWT, password hashing, Google OAuth verify
│   │   ├── database.py         # Async SQLAlchemy engine / session / Base
│   │   └── logging.py          # Centralised logging
│   ├── services/
│   │   ├── gcs_service.py      # Google Cloud Storage helpers
│   │   ├── crew_runner.py      # Async wrapper that drives the crew per job
│   │   ├── crew/               # CrewAI pipeline
│   │   │   ├── agents.py       #   Agent definitions + tools
│   │   │   ├── tasks.py        #   Task definitions
│   │   │   ├── crew.py         #   build_crew() factory
│   │   │   └── __main__.py     #   CLI: python -m app.services.crew
│   │   └── extractors/
│   │       ├── linkedin_extractor.py  # Bright Data MCP → JobDetails
│   │       └── github_extractor.py    # GitHub → Weaviate vector store (RAG)
│   ├── models/                 # SQLAlchemy ORM models
│   ├── schemas/                # Pydantic request/response schemas
│   └── main.py                 # FastAPI app init, CORS, router registration
├── credentials/                # GCP service-account / OAuth JSON (gitignored)
├── sample_data/                # Example resume & interview output
├── golden_dataset/
│   └── agents/resume_analyzer/ # Golden dataset for RAGAS eval (versioned: v1/, ...)
├── tests/                      # Pytest suite
│   └── ragas/                  # RAGAS eval harness + run results (see below)
├── pyproject.toml              # Dependencies (managed by uv)
├── Dockerfile                  # (intentionally empty for now)
└── .env.example                # Copy to .env and fill in
```

## Setup (uv)

This project uses [uv](https://docs.astral.sh/uv/).

```bash
cd backend
cp .env.example .env          # fill in secrets
uv sync                       # create venv + install from pyproject.toml
```

## Custom commands

Defined as `[project.scripts]` in `pyproject.toml` — run from `backend/`:

| Command            | What it does                                              |
| ------------------ | -------------------------------------------------------- |
| `uv run api`       | FastAPI dev server with autoreload (127.0.0.1:8000)      |
| `uv run api-prod`  | FastAPI production server (no reload, `API_WORKERS` proc) |
| `uv run frontend`  | Vite dev server via `npm run dev` (../frontend)          |
| `uv run dev`       | Runs **db-proxy + api + frontend** together; Ctrl-C stops all |
| `uv run db-proxy`  | Cloud SQL Auth Proxy → `127.0.0.1:3306` (standalone)      |
| `uv run crew ...`  | The CrewAI pipeline CLI (see below)                      |

Override host/port/workers with env vars: `API_HOST`, `API_PORT`, `API_WORKERS`.

`db-proxy` derives the instance connection name from `.env`
(`<GCP_PROJECT_ID>:<GCP_LOCATION>:job-applier-mysql`) and binds the port from
`MYSQL_PORT`. Override with `CLOUD_SQL_INSTANCE` (full `project:region:instance`
name), `CLOUD_SQL_INSTANCE_NAME`, or `CLOUD_SQL_PROXY_BIN`. It authenticates with
`GOOGLE_APPLICATION_CREDENTIALS`, so that must point at a valid service-account
key with the **Cloud SQL Client** role.

```bash
cd backend
uv run api          # → http://localhost:8000/docs
uv run dev          # db-proxy + api + frontend (needs `npm install` in ../frontend first)
```

`dev` starts the Cloud SQL proxy first and waits until `127.0.0.1:<MYSQL_PORT>`
accepts connections before launching the API, so the app's startup DB connection
succeeds. Skip the proxy with `DEV_SKIP_PROXY=1` (e.g. when `MYSQL_HOST` is a
direct public IP). Ctrl-C stops all three processes (and their children).

## Run the crew pipeline standalone (CLI)

```bash
cd backend
uv run crew \
    --url https://www.linkedin.com/jobs/view/<id>/ \
    --github https://github.com/<user> \
    --resume sample_data/Arijit_De_Resume.md \
    --name "Your Name"
```

> The CLI intentionally stays **Markdown-only** — the .docx generation below is
> a web-app feature (it needs the job/user identity for GCS and Cloud SQL).

## DOCX resume generation (+ PDF preview)

In the web app, the Resume Strategist agent does more than write Markdown:
after tailoring the content it assembles it into a structured resume JSON and
calls the per-run **`generate_resume_docx`** tool
([app/services/crew/docx_tools.py](app/services/crew/docx_tools.py)), which
POSTs to an external document service (`RESUME_DOCX_SERVICE_URL`) and receives
a polished Word (.docx) resume named
`{applicant_name}_{company_name}_{job_name}_resume.docx`.

- **Retries / fallback:** the agent gets at most **3 tool attempts**; the tool
  enforces the cap. If the service stays unavailable, the run completes
  exactly as before — the Markdown resume is uploaded and served.
- **PDF preview:** a generated .docx is converted to PDF with **LibreOffice
  headless** ([app/services/docx_to_pdf.py](app/services/docx_to_pdf.py)). The
  frontend's *View Resume* renders that PDF; *Download* serves the .docx. The
  Markdown version is always uploaded too and is the preview fallback whenever
  no PDF exists.
- **Storage:** `jobs.tailored_resume_gcs_path` (download target — .docx when
  generated, else .md), `jobs.tailored_resume_md_gcs_path` (always),
  `jobs.tailored_resume_pdf_gcs_path` (nullable). The columns are added by the
  lightweight startup migration in `app/main.py`.

**System dependency — LibreOffice** (for the docx→pdf conversion):

```bash
sudo apt-get install -y libreoffice-writer fonts-liberation
```

Missing LibreOffice degrades gracefully: the .docx is still generated and
downloadable, the in-browser preview just falls back to Markdown.

**Env vars** (in `backend/.env`, see `.env.example`):

```env
RESUME_DOCX_SERVICE_URL=http://resume-service-alb-1115872566.us-east-2.elb.amazonaws.com/api/v1/resume/generate
RESUME_DOCX_TIMEOUT_SECONDS=120
```

## Tests

```bash
cd backend
uv run pytest
```

## RAGAS evaluation (resume_analyzer)

Scores the `resume_analyzer` agent's JSON output against a hand-verified golden
dataset on three metrics: **faithfulness**, **answer_relevancy**,
**answer_correctness**. Design and details:
[docs/resume_analyzer_ragas_evaluation_plan.md](docs/resume_analyzer_ragas_evaluation_plan.md).

**Prerequisites** (in `backend/.env`):

- `ANTHROPIC_API_KEY` — the agent under test runs on Claude
  (skippable with `--answers-from`, see below)
- `GEMINI_API_KEY` — the RAGAS judge LLM + embeddings run on Gemini

**1. Check / curate the golden dataset** (`golden_dataset/agents/resume_analyzer/v1/`).
Each sample dir holds `context.txt` (extracted resume text), `ground_truth.json`
(hand-verified `ParsedResume` JSON) and `meta.json`. Hand-check every
`ground_truth.json` field against the resume, then set `"verified": true` in its
`meta.json` — the runner warns when it scores unverified ground truths.

**2. Run the evaluation** (from `backend/`):

```bash
# Full run: all samples — runs the agent live, then scores (~15-30 min)
uv run python -m tests.ragas.agents.resume_analyzer.run_eval --version v1

# Smoke run: first sample only
uv run python -m tests.ragas.agents.resume_analyzer.run_eval --version v1 --limit 1

# Re-score cached answers from a previous run (no agent runs, no ANTHROPIC key)
uv run python -m tests.ragas.agents.resume_analyzer.run_eval --version v1 \
    --answers-from tests/ragas/agents/resume_analyzer/v1/run_<timestamp>
```

**3. Read the results** in
`tests/ragas/agents/resume_analyzer/<version>/run_<timestamp>/`:

| File              | Contents                                              | Git |
| ----------------- | ----------------------------------------------------- | --- |
| `summary.json`    | Per-metric means, sample list, NaN counts             | tracked |
| `run_config.json` | Models, verbalizer id, git commit — reproducibility   | tracked |
| `scores.csv`      | Per-sample per-metric scores                          | ignored |
| `answers/*.json`  | The raw agent outputs that were scored                | ignored |

**Adding a sample:** drop the resume into `sample_data/`, add it to `SOURCES` in
`tests/ragas/agents/resume_analyzer/bootstrap_dataset.py`, run
`uv run python -m tests.ragas.agents.resume_analyzer.bootstrap_dataset --version v1`
(regenerates `context.txt`/`manifest.json`; never overwrites existing ground
truths), then hand-verify the new `ground_truth.json`. Bump to `v2/` when
changing an existing dataset version's samples or ground truths.
