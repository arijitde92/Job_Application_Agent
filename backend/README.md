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
│   │       └── github_extractor.py    # GitHub → BigQuery vector store (RAG)
│   ├── models/                 # SQLAlchemy ORM models
│   ├── schemas/                # Pydantic request/response schemas
│   └── main.py                 # FastAPI app init, CORS, router registration
├── credentials/                # GCP service-account / OAuth JSON (gitignored)
├── sample_data/                # Example resume & interview output
├── tests/                      # Pytest suite
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

## Tests

```bash
cd backend
uv run pytest
```
