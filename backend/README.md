# Job Application Agent — Backend

FastAPI backend plus the multi-agent pipeline that tailors resumes and
generates interview materials from a LinkedIn job posting (or a pasted job
description) and a GitHub profile. The agents are
[LangChain](https://python.langchain.com/) 1.x `create_agent` agents,
orchestrated as a [LangGraph](https://langchain-ai.github.io/langgraph/) state
graph and traced with [LangSmith](https://smith.langchain.com/).

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
│   │   ├── pipeline_runner.py  # Async wrapper: runs the graph per job, streams progress
│   │   ├── pipeline/           # LangGraph pipeline (LangChain agents)
│   │   │   ├── graph.py        #   StateGraph: nodes, parallel fan-out/fan-in, build_pipeline()
│   │   │   ├── agents.py       #   AgentSpecs (role/goal/backstory) → create_agent builders
│   │   │   ├── prompts.py      #   Task descriptions + safe {placeholder} interpolation
│   │   │   ├── llms.py         #   Gemini / Claude / GLM-5.2 factories, rate limiter, registry
│   │   │   ├── middleware.py   #   Tool-error, iteration-cap and resume-guardrail middleware
│   │   │   ├── guardrails.py   #   Claude faithfulness judge for the parsed resume
│   │   │   ├── schemas.py      #   Structured output (GithubReposSummary)
│   │   │   ├── tracing.py      #   LangSmith run config + run-log callback
│   │   │   ├── tools/          #   LangChain @tool tools (resume, docx, GitHub, web)
│   │   │   └── __main__.py     #   CLI: python -m app.services.pipeline
│   │   └── extractors/
│   │       ├── linkedin_extractor.py  # Bright Data MCP (langchain-mcp-adapters) → JobDetails
│   │       ├── text_job_extractor.py  # Pasted/uploaded job description → JobDetails
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
├── Dockerfile                  # Cloud Run image (uv + Tectonic, see docs/GCP_deployment.md)
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
| `uv run pipeline ...` | The LangGraph pipeline CLI (see below)                |

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

## The agent pipeline

One job run is a LangGraph `StateGraph`
([app/services/pipeline/graph.py](app/services/pipeline/graph.py)):

```
START ──┬──> github_summary ───┐
        │                      ├──> profile ──> resume_strategy ──> interview_preparation ──> END
        └──> resume_analysis ──┘
```

- `github_summary` and `resume_analysis` run **in parallel**; `profile` starts
  only when both have finished. Without a GitHub profile the `github_summary`
  node is left out and the remaining prompts carry explicit grounding rules.
- Each node runs one LangChain `create_agent` agent
  ([agents.py](app/services/pipeline/agents.py)) with a shared middleware
  stack ([middleware.py](app/services/pipeline/middleware.py)): tool errors
  become observations the model can recover from, a per-agent step budget
  forces a final answer, and long conversations are summarized before they
  outgrow the context window.
- The resume analyzer's output is checked by `ParsedResumeGuardrailMiddleware`:
  the save must have succeeded, every URL must be valid, and a **Claude
  faithfulness judge** ([guardrails.py](app/services/pipeline/guardrails.py))
  must score the JSON ≥ 7/10 against the resume text. A failure sends feedback
  back to the agent (up to 3 retries) and otherwise aborts the run before any
  downstream node starts.
- Tools that carry per-run identity (the applicant's resume, user/resume ids,
  GitHub account, job/output path) are built per run by factories in
  [tools/](app/services/pipeline/tools/), so the LLM never chooses whose data
  it touches.
- Models ([llms.py](app/services/pipeline/llms.py)): GLM-5.2 via Z.ai
  (`langchain-openai`) for the GitHub summarizer, Claude Sonnet 5
  (`langchain-anthropic`) for the resume analyzer and its judge, Gemini 3.5
  Flash Lite (`langchain-google-genai`) for the profiler, strategist and
  interview preparer. Each agent role has its own 10-requests-per-minute
  rate limiter.

## Run the pipeline standalone (CLI)

```bash
cd backend
uv run pipeline \
    --url https://www.linkedin.com/jobs/view/<id>/ \
    --github https://github.com/<user> \
    --resume "sample_data/Arijit De Resume 2026.md" \
    --name "Your Name"
```

It writes `<applicant>_<company>_<job>_resume.md` and `interview_materials.md`
to the current directory and logs per-model token usage. Pass `--github ""` to
run without GitHub.

> The CLI intentionally stays **Markdown-only** — the .docx generation below is
> a web-app feature (it needs the job/user identity for GCS and Cloud SQL).

## Tracing (LangSmith)

Tracing is off by default and controlled by environment variables in
`backend/.env` (exported to the process by `app.core.config.get_settings()`):

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=job-application-agent
LANGSMITH_ENDPOINT=              # optional (EU / self-hosted)
```

With tracing on, every job — web app, CLI or RAGAS run — produces **one
trace**: the `tailor_resume_job` root span (job extraction, resume parsing),
the LangGraph run with its two parallel branches, each agent as a named
subgraph with every model and tool call, and the faithfulness judge. Web runs
carry `job_id`, `user_id` and `resume_id` metadata and `web` / `with-github` /
`no-github` tags for filtering ([tracing.py](app/services/pipeline/tracing.py)).

> **Privacy:** traces contain resume text and other personal data. Enable
> tracing only for a LangSmith workspace you are allowed to store applicant
> data in.

Independently of LangSmith, every web run also writes a readable step-by-step
log (node starts/ends, tool calls, model answers) to
`backend/logs/<timestamp>_job<id>_pipeline.log`.

## PDF resume generation (LaTeX → PDF)

In the web app the final tailored resume is a **PDF compiled from LaTeX**,
built from the template in
[app/services/latex/templates/resume.tex.j2](app/services/latex/templates/resume.tex.j2).
After tailoring the content, the Resume Strategist agent calls two
deterministic per-run tools
([app/services/pipeline/tools/latex_tools.py](app/services/pipeline/tools/latex_tools.py)):

1. **`generate_resume_latex`** — its arguments are the tailored resume,
   validated by the Pydantic schema
   [`TailoredResume`](app/services/latex/schemas.py) (dates normalised to
   `SEP 2025` / `PRESENT`, URLs normalised, `**bold**` balanced, …). The
   renderer ([app/services/latex/renderer.py](app/services/latex/renderer.py))
   escapes every value (no LaTeX injection) and produces byte-identical
   output for identical input. Section order is decided deterministically
   by [app/services/latex/layout.py](app/services/latex/layout.py):
   - internships (`is_internship: true`, or "Intern"/"Internship" in the
     title) are shown only when the candidate has no other work experience;
   - when total non-intern experience (merged date ranges, in months) is
     none or under 1 year, the order is Summary → Education → Skills → Work
     Experience → Projects → Certifications → Publications; otherwise
     Summary → Work Experience → Education → Skills → Projects →
     Publications → Certifications.

   The `.tex` is uploaded to
   `gs://<bucket>/tailored/<user_id>/<job_id>_<applicant>_<company>_<job>_resume.tex`
   and recorded on `jobs.tailored_resume_latex_gcs_path`.
2. **`compile_resume_pdf`** (no arguments) — compiles that `.tex` with
   **Tectonic** ([app/services/latex/compiler.py](app/services/latex/compiler.py),
   `--untrusted`). A compile error is returned to the agent, which fixes the
   content and regenerates.

Each tool allows 3 attempts; if the LaTeX route fails for good, the agent
falls back to the `.docx` service below.

**The PDF is never stored.** It lives only in a temp directory and in a
short-lived server-side cache
([app/services/latex/pdf_cache.py](app/services/latex/pdf_cache.py),
`RESUME_PDF_CACHE_TTL_MINUTES`). *View* and *Download*
(`GET /api/jobs/{id}/resume/pdf`, `/resume/download`) compile the stored
`.tex` on demand with the same function — so a returning user always gets
their PDF regenerated from GCS.

**System dependency — [Tectonic](https://tectonic-typesetting.github.io):**

```bash
# Linux (single binary); macOS: brew install tectonic; Windows: scoop install tectonic
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh
```

The first compile downloads the TeX packages it needs (~3–4 min) and caches
them; later compiles take ~2 s. Warm the cache once before running real jobs
— a cold compile exceeds `LATEX_COMPILE_TIMEOUT_SECONDS` (90 s) and the agent
would fall back to docx. Set `TECTONIC_PATH` if the binary is not on
`PATH`, and `TECTONIC_ONLY_CACHED=true` once the cache is pre-warmed (e.g. in
the Docker image) so compiling never needs the network.

## DOCX resume generation (fallback)

When the LaTeX route fails, the Resume Strategist agent falls back to the
per-run **`generate_resume_docx`** tool
([app/services/pipeline/tools/docx_tools.py](app/services/pipeline/tools/docx_tools.py)), which
POSTs to an external document service (`RESUME_DOCX_SERVICE_URL`) and receives
a polished Word (.docx) resume named
`{applicant_name}_{company_name}_{job_name}_resume.docx`.

- **Layout:** the tool applies the same rules as the LaTeX route
  ([app/services/latex/layout.py](app/services/latex/layout.py)) to the
  metadata `section_order` and the experience list — internships dropped when
  there is actual work experience, and the early-career order when total
  experience is none or under 1 year. Experienced candidates keep the
  service's default order (Summary → Experience → Education → Skills →
  Publications → Projects → Certifications). `is_internship` is stripped
  before the request.
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

The suite runs offline — no API keys, no network. The pipeline tests drive the
real LangChain agents, middleware and tools with scripted fake chat models
([tests/lc_fakes.py](tests/lc_fakes.py)):

| Test module | Covers |
| ----------- | ------ |
| `test_pipeline_graph.py` | Graph topology, parallel fan-out/fan-in, full runs with/without GitHub, guardrail abort, search scoping |
| `test_pipeline_middleware.py` | Tool-error recovery, iteration cap / forced final answer, resume guardrail + faithfulness judge |
| `test_llms.py` | Model factories (payloads, no OpenAI fallback for GLM), rate limiter, model registry |
| `test_prompts.py` | Placeholder interpolation, rendered task messages |
| `test_tool_schemas.py` | Tool names / argument schemas as the LLM sees them, per-run identity binding |
| `test_calculate_yoe.py`, `test_docx_tools.py`, `test_web_tools.py` | The tools, invoked as LangChain tools |
| `test_linkedin_extractor_mcp.py` | Bright Data MCP via `langchain-mcp-adapters` (faked) |
| `test_pipeline_runner.py`, `test_tracing.py` | Progress streaming, result assembly, LangSmith config |

Opt-in live test: `RUN_LIVE_LLM_TESTS=1 uv run pytest -k live` (needs
`GEMINI_API_KEY`). The resume analyzer can be run live in isolation with
`uv run python tests/run_resume_analyzer_test.py --resume <file>` (needs
`ANTHROPIC_API_KEY`).

## RAGAS evaluation (resume_analyzer)

Scores the `resume_analyzer` agent's JSON output against a hand-verified golden
dataset on three metrics: **faithfulness**, **answer_relevancy**,
**answer_correctness**. Design and details:
[docs/resume_analyzer_ragas_evaluation_plan.md](docs/resume_analyzer_ragas_evaluation_plan.md).

The agent under test is produced by `app.services.pipeline.analyze_resume` —
the same LangChain agent, prompt and guardrail middleware the pipeline's
`resume_analysis` node runs. With `LANGSMITH_TRACING=true` each sample is a
trace tagged `ragas`.

**Prerequisites** (in `backend/.env`):

- `ANTHROPIC_API_KEY` — the agent under test and its faithfulness judge run on
  Claude (skippable with `--answers-from`, see below)
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
