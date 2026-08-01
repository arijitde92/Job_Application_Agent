# GCP Deployment Guide

How to deploy the **Job Application Agent** (FastAPI backend + React/Vite
frontend) to Google Cloud using **Cloud Run**.

This guide assumes the base GCP resources already exist — project, Cloud SQL
instance, GCS bucket, service account, OAuth client. If not, complete
[GCP_Setup.md](GCP_Setup.md) first.

---

## Architecture

```
                        ┌────────────────────────── Google Cloud ─────────────────────────┐
                        │                                                                 │
  Browser ── HTTPS ──►  │  Cloud Run: job-agent-web          Cloud Run: job-agent-api     │
                        │  (nginx serves the Vite SPA,       ┌───────────┬─────────────┐  │
                        │   proxies /api/* to the API) ────► │ FastAPI   │ Cloud SQL   │  │
                        │                                    │ (uvicorn) │ Auth Proxy  │  │
                        │                                    └─────┬─────┴──────┬──────┘  │
                        │                                          │            │         │
                        │                                    GCS bucket    Cloud SQL      │
                        │                                    (resumes)     (MySQL 8)      │
                        └─────────────────────────────────────────┼───────────────────────┘
                                                                  │
                                    External SaaS: Anthropic, Gemini, Z.ai, Weaviate Cloud,
                                    Voyage AI, Bright Data (remote MCP), Serper, GitHub API
```

- **Backend** — the FastAPI app in a container, with the **Cloud SQL Auth Proxy
  as a sidecar** (the app builds a TCP MySQL URL from `MYSQL_HOST:MYSQL_PORT`,
  so the proxy listens on `127.0.0.1:3306` exactly as in local dev).
- **Frontend** — the built SPA served by nginx on a second Cloud Run service.
  nginx proxies `/api/*` to the backend, so the app is a **single origin**: the
  default `VITE_API_BASE_URL=/api` keeps working and CORS is a non-issue.
- **Secrets** — Secret Manager, injected as env vars.
- **Credentials** — no `service-account.json` in the cloud. The Cloud Run
  *runtime service account* provides Application Default Credentials;
  `GOOGLE_APPLICATION_CREDENTIALS` stays **unset** (the app only exports it
  when non-empty, and `google-cloud-storage` then falls back to ADC).

### ⚠️ Read this first — runtime constraints

Two implementation details dictate how the backend must be deployed:

1. **Tailoring jobs run in-process.** `POST /api/jobs/tailor` starts the CrewAI
   pipeline with `asyncio.create_task(...)` inside the API process — there is no
   queue or worker service. If the instance handling the job is throttled or
   shut down, the job dies (startup cleanup later marks jobs stuck >3h as
   failed).
2. **Progress is stored in process memory.** The SSE endpoint
   `GET /api/jobs/{id}/progress` reads `crew_runner.progress_store`, a plain
   dict. The SSE request must land on the *same process* that runs the job.

Therefore the backend service **must** run with:

| Setting | Value | Why |
|---|---|---|
| `--min-instances` / `--max-instances` | `1` / `1` | one instance → SSE always finds the job's `progress_store`; jobs aren't lost to scale-in |
| `--no-cpu-throttling` | on | crew keeps CPU after the HTTP request returns |
| `--timeout` | `3600` | long SSE streams and downloads |
| uvicorn workers | `1` | multiple workers = multiple processes = separate `progress_store`s |

> One instance with ~80 concurrent requests is plenty for a personal/demo
> deployment. To scale horizontally later, move `progress_store` to the DB or
> Redis and run the crew in a Cloud Run **Job** or a task queue — see
> [Hardening ideas](#hardening-ideas) at the end.

---

## 1. Prerequisites

- [GCP_Setup.md](GCP_Setup.md) completed: project, **Cloud SQL** instance
  `job-applier-mysql` (region `asia-south2`) with database `job_applier`, **GCS
  bucket**, service account `job-agent-sa` with roles `Cloud SQL Client` +
  `Storage Object Admin`, and (optionally) the Google OAuth client.
- `gcloud` CLI authenticated: `gcloud auth login && gcloud config set project YOUR_PROJECT_ID`
- All third-party keys at hand (same list as `backend/.env.example`):
  Anthropic, Gemini, Z.ai, Weaviate Cloud, Voyage AI, Bright Data, Serper,
  GitHub PAT.

Set these shell variables once — every command below uses them:

```bash
export PROJECT_ID=YOUR_PROJECT_ID
export REGION=asia-south2
export SA_EMAIL="job-agent-sa@${PROJECT_ID}.iam.gserviceaccount.com"
export REPO="job-agent"                       # Artifact Registry repo name
export SQL_INSTANCE="${PROJECT_ID}:${REGION}:job-applier-mysql"
```

## 2. Enable deployment APIs

```bash
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    --project=$PROJECT_ID
```

## 3. One-time infrastructure

### 3a. Artifact Registry repository (container images)

```bash
gcloud artifacts repositories create $REPO \
    --repository-format=docker \
    --location=$REGION \
    --project=$PROJECT_ID
```

### 3b. Runtime service account permissions

`job-agent-sa` already has `cloudsql.client` and `storage.objectAdmin` from
GCP_Setup.md. Add access to secrets:

```bash
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/secretmanager.secretAccessor"
```

## 4. Put secrets in Secret Manager

Create one secret per sensitive value (names match the env vars the app reads):

```bash
for s in MYSQL_PASSWORD JWT_SECRET_KEY ANTHROPIC_API_KEY GEMINI_API_KEY \
         ZAI_API_KEY WEAVIATE_API_KEY VOYAGE_API_KEY \
         GITHUB_PERSONAL_ACCESS_TOKEN SERPER_API_KEY BRIGHT_DATA_API_KEY \
         GOOGLE_OAUTH_CLIENT_SECRET; do
  printf 'REPLACE_WITH_REAL_VALUE' | gcloud secrets create $s \
      --data-file=- --replication-policy=automatic --project=$PROJECT_ID
done
```

Replace a value later with:

```bash
printf 'the-new-value' | gcloud secrets versions add MYSQL_PASSWORD --data-file=-
```

> Generate a production `JWT_SECRET_KEY` with
> `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
> Skip `GOOGLE_OAUTH_CLIENT_SECRET` / `ZAI_API_KEY` if you don't use those
> features — their env vars can simply be omitted from the service.

## 5. Build the backend image

`backend/Dockerfile` is intentionally empty in the repo. Fill it with the
following (a standard two-stage [uv](https://docs.astral.sh/uv/) build — deps
are installed from `uv.lock`, only `app/` is shipped):

```dockerfile
# backend/Dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Cloud Run injects PORT (default 8080). Exactly ONE worker — see
# "runtime constraints": job progress lives in process memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
```

Add a `backend/.dockerignore` so the build context stays small and **no
credentials or local artifacts leak into the image**:

```
.venv/
.env
credentials/
logs/
tests/
golden_dataset/
sample_data/
docs/
__pycache__/
*.pyc
```

Build and push with Cloud Build (run from the repo root):

```bash
export API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:v1"
gcloud builds submit backend --tag $API_IMAGE --project=$PROJECT_ID
```

## 6. Deploy the backend to Cloud Run

The service runs **two containers**: the API and the Cloud SQL Auth Proxy
sidecar. Multi-container services are easiest to declare as YAML.

Create `service-api.yaml` (do **not** commit it if you inline real values —
the version below only references secrets, so it is safe to keep):

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: job-agent-api
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "1"
        autoscaling.knative.dev/maxScale: "1"
        run.googleapis.com/cpu-throttling: "false"
        # start the API only after the proxy's startup probe passes
        run.googleapis.com/container-dependencies: '{"api":["cloud-sql-proxy"]}'
    spec:
      serviceAccountName: job-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
      timeoutSeconds: 3600
      containerConcurrency: 80
      containers:
        - name: api
          image: asia-south2-docker.pkg.dev/YOUR_PROJECT_ID/job-agent/backend:v1
          ports:
            - containerPort: 8080
          resources:
            limits:
              cpu: "2"
              memory: 2Gi
          startupProbe:
            httpGet:
              path: /api/health
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 30
          env:
            # ── plain config ──────────────────────────────────────────────
            - {name: GCP_PROJECT_ID,          value: "YOUR_PROJECT_ID"}
            - {name: GCP_LOCATION,            value: "asia-south2"}
            - {name: GCS_BUCKET_NAME,         value: "job-applier-YOUR_PROJECT_ID-resumes"}
            - {name: MYSQL_HOST,              value: "127.0.0.1"}   # the sidecar
            - {name: MYSQL_PORT,              value: "3306"}
            - {name: MYSQL_USER,              value: "root"}
            - {name: MYSQL_DATABASE,          value: "job_applier"}
            - {name: WEAVIATE_URL,            value: "your-cluster.c0.region.gcp.weaviate.cloud"}
            - {name: WEAVIATE_COLLECTION_NAME, value: "GithubRepoData"}
            - {name: VOYAGE_EMBED_MODEL,      value: "voyage-code-3"}
            - {name: VOYAGE_EMBED_DIMENSION,  value: "1024"}
            - {name: VOYAGE_RERANK_MODEL,     value: "rerank-2.5-lite"}
            - {name: JWT_ALGORITHM,           value: "HS256"}
            - {name: JWT_EXPIRY_MINUTES,      value: "1440"}
            - {name: GOOGLE_OAUTH_CLIENT_ID,  value: "your-client-id.apps.googleusercontent.com"}
            - {name: LOG_LEVEL,               value: "INFO"}
            - {name: MAX_RESUME_SIZE_MB,      value: "10"}
            - {name: CREWAI_TOOLS_ALLOW_UNSAFE_PATHS, value: "true"}
            - {name: CREWAI_TRACING_ENABLED,  value: "false"}
            # set after the frontend exists (step 8); harmless meanwhile
            - {name: CORS_ORIGINS,            value: "https://job-agent-web-PLACEHOLDER.a.run.app"}
            # NOTE: GOOGLE_APPLICATION_CREDENTIALS is deliberately ABSENT —
            # ADC uses the runtime service account.
            # ── secrets ───────────────────────────────────────────────────
            - name: MYSQL_PASSWORD
              valueFrom: {secretKeyRef: {name: MYSQL_PASSWORD, key: latest}}
            - name: JWT_SECRET_KEY
              valueFrom: {secretKeyRef: {name: JWT_SECRET_KEY, key: latest}}
            - name: ANTHROPIC_API_KEY
              valueFrom: {secretKeyRef: {name: ANTHROPIC_API_KEY, key: latest}}
            - name: GEMINI_API_KEY
              valueFrom: {secretKeyRef: {name: GEMINI_API_KEY, key: latest}}
            - name: ZAI_API_KEY
              valueFrom: {secretKeyRef: {name: ZAI_API_KEY, key: latest}}
            - name: WEAVIATE_API_KEY
              valueFrom: {secretKeyRef: {name: WEAVIATE_API_KEY, key: latest}}
            - name: VOYAGE_API_KEY
              valueFrom: {secretKeyRef: {name: VOYAGE_API_KEY, key: latest}}
            - name: GITHUB_PERSONAL_ACCESS_TOKEN
              valueFrom: {secretKeyRef: {name: GITHUB_PERSONAL_ACCESS_TOKEN, key: latest}}
            - name: SERPER_API_KEY
              valueFrom: {secretKeyRef: {name: SERPER_API_KEY, key: latest}}
            - name: BRIGHT_DATA_API_KEY
              valueFrom: {secretKeyRef: {name: BRIGHT_DATA_API_KEY, key: latest}}
            - name: GOOGLE_OAUTH_CLIENT_SECRET
              valueFrom: {secretKeyRef: {name: GOOGLE_OAUTH_CLIENT_SECRET, key: latest}}
        # ── Cloud SQL Auth Proxy sidecar ──────────────────────────────────
        - name: cloud-sql-proxy
          image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.2  # pin latest 2.x
          args:
            - "--address=127.0.0.1"     # containers share the network namespace
            - "--port=3306"
            - "YOUR_PROJECT_ID:asia-south2:job-applier-mysql"
          startupProbe:
            tcpSocket:
              port: 3306
            periodSeconds: 2
            failureThreshold: 30
```

Replace every `YOUR_PROJECT_ID` (and the Weaviate/OAuth values), then deploy:

```bash
gcloud run services replace service-api.yaml --region=$REGION --project=$PROJECT_ID

# The API enforces its own JWT auth — allow unauthenticated HTTP:
gcloud run services add-iam-policy-binding job-agent-api \
    --region=$REGION --member="allUsers" --role="roles/run.invoker" \
    --project=$PROJECT_ID
```

Verify (tables are auto-created on first startup — see
[migrations.md](migrations.md)):

```bash
export API_URL=$(gcloud run services describe job-agent-api \
    --region=$REGION --format='value(status.url)')
curl "$API_URL/api/health"
# → {"status":"healthy","service":"job-application-agent"}
```

### Alternative DB connectivity (instead of the sidecar)

- **Private IP + Direct VPC egress** — enable private IP on the Cloud SQL
  instance, deploy the service with `--network`/`--subnet`, and set
  `MYSQL_HOST` to the instance's private IP. Cleaner long-term, but requires
  VPC + Service Networking setup.
- **Public IP with authorized networks** (as used for local dev in
  GCP_Setup.md §5c) does **not** work from Cloud Run — instances have no
  stable egress IP unless you add a VPC connector + Cloud NAT. Don't
  authorize `0.0.0.0/0`.

## 7. Build the frontend image

The SPA is compiled by Vite and served by nginx, which also proxies `/api/*`
to the backend — one origin, no CORS, and the default `VITE_API_BASE_URL=/api`
from `frontend/.env.example` needs no change.

`frontend/Dockerfile` (also intentionally empty in the repo — fill with):

```dockerfile
# frontend/Dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Vite bakes VITE_* vars into the bundle at BUILD time
ARG VITE_API_BASE_URL=/api
ARG VITE_GOOGLE_OAUTH_CLIENT_ID
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_GOOGLE_OAUTH_CLIENT_ID=$VITE_GOOGLE_OAUTH_CLIENT_ID
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# templates/*.template get envsubst'ed at container start (BACKEND_HOST)
COPY nginx.conf /etc/nginx/templates/default.conf.template
```

`frontend/nginx.conf` (new file — `${BACKEND_HOST}` is substituted from the
env at startup; nginx runtime vars like `$uri` are left alone):

```nginx
server {
    listen 8080;
    root /usr/share/nginx/html;
    index index.html;

    # resume uploads (MAX_RESUME_SIZE_MB=10 + headroom); nginx default is 1m!
    client_max_body_size 15m;

    location /api/ {
        proxy_pass https://${BACKEND_HOST};
        proxy_set_header Host ${BACKEND_HOST};
        proxy_ssl_server_name on;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        # SSE (/api/jobs/{id}/progress): stream, don't buffer, don't time out
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    location / {
        try_files $uri /index.html;   # SPA fallback for react-router
    }
}
```

`frontend/.dockerignore`:

```
node_modules/
dist/
.env*
```

Because `gcloud builds submit --tag` cannot pass `--build-arg`, add a tiny
`frontend/cloudbuild.yaml`:

```yaml
steps:
  - name: gcr.io/cloud-builders/docker
    args:
      - build
      - --build-arg
      - VITE_GOOGLE_OAUTH_CLIENT_ID=${_OAUTH_CLIENT_ID}
      - -t
      - ${_IMAGE}
      - .
images: ["${_IMAGE}"]
substitutions:
  _IMAGE: ""
  _OAUTH_CLIENT_ID: ""
```

Build and push (from the repo root):

```bash
export WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/frontend:v1"
gcloud builds submit frontend \
    --config frontend/cloudbuild.yaml \
    --substitutions=_IMAGE=$WEB_IMAGE,_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com \
    --project=$PROJECT_ID
```

## 8. Deploy the frontend

```bash
# host only, no scheme — used in nginx's proxy_pass
export BACKEND_HOST=$(echo $API_URL | sed 's|https://||')

gcloud run deploy job-agent-web \
    --image=$WEB_IMAGE \
    --region=$REGION \
    --allow-unauthenticated \
    --port=8080 \
    --set-env-vars=BACKEND_HOST=$BACKEND_HOST \
    --memory=256Mi --cpu=1 \
    --min-instances=0 --max-instances=2 \
    --timeout=3600 \
    --project=$PROJECT_ID

export WEB_URL=$(gcloud run services describe job-agent-web \
    --region=$REGION --format='value(status.url)')
echo $WEB_URL
```

(The frontend `--timeout=3600` matters: proxied SSE connections are subject to
*this* service's request timeout too.)

## 9. Post-deploy wiring

1. **CORS** — update the backend's `CORS_ORIGINS` to the real frontend URL
   (defense in depth; same-origin proxying means the browser normally never
   makes a cross-origin call):

   ```bash
   gcloud run services update job-agent-api --region=$REGION \
       --update-env-vars=CORS_ORIGINS=$WEB_URL
   ```

2. **Google OAuth** (if using "Sign in with Google") — in
   [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials),
   add `$WEB_URL` to the OAuth client's **Authorized JavaScript origins** (and
   redirect URIs if applicable).

3. **End-to-end check** — open `$WEB_URL`, register a user, upload a resume
   (lands in the GCS bucket), add a GitHub profile, run a tailoring job and
   watch the live progress stream, then download the tailored resume and
   interview materials.

## 10. Updating / redeploying

```bash
# Backend: bump the tag, rebuild, edit service-api.yaml's image, re-apply
export API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:v2"
gcloud builds submit backend --tag $API_IMAGE
sed -i 's|/backend:v[0-9]*|/backend:v2|' service-api.yaml
gcloud run services replace service-api.yaml --region=$REGION

# Frontend
export WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/frontend:v2"
gcloud builds submit frontend --config frontend/cloudbuild.yaml \
    --substitutions=_IMAGE=$WEB_IMAGE,_OAUTH_CLIENT_ID=...
gcloud run deploy job-agent-web --image=$WEB_IMAGE --region=$REGION
```

> **Deploys interrupt running jobs.** A new revision replaces the single
> backend instance, killing any in-flight crew pipeline; the startup cleanup
> will mark such jobs failed after 3 h so users can retry. Deploy when no
> jobs are running.

Rollback: `gcloud run services update-traffic job-agent-api --to-revisions=REVISION=100 --region=$REGION`.

## 11. Logs & monitoring

```bash
gcloud run services logs tail job-agent-api --region=$REGION   # live tail
gcloud run services logs read job-agent-api --region=$REGION --limit=100
```

Both stdout (app + uvicorn) and the sidecar's proxy logs land in **Cloud
Logging** (Console → Logging → Logs Explorer, resource "Cloud Run Revision").
The rotating file log in `logs/job_agent.log` also exists inside the container
but is ephemeral — rely on Cloud Logging. Consider an uptime check on
`$API_URL/api/health` (Console → Monitoring → Uptime checks).

## 12. Cost notes (approximate)

| Item | Config | ~Cost/month |
|---|---|---|
| Cloud Run backend | 1 always-on instance, 2 vCPU / 2 GiB, CPU always allocated | ~$100 (halve it with 1 vCPU / 1 GiB if the crew fits) |
| Cloud Run frontend | scale-to-zero nginx | ~$0–1 |
| Cloud SQL | `db-f1-micro`, 10 GB SSD | ~$7–10 |
| GCS, Artifact Registry, Secret Manager, egress | light usage | ~$1–5 |

The dominant cost is the always-on backend — a direct consequence of running
jobs in-process (see constraints above). LLM/API usage (Anthropic, Voyage,
Bright Data, …) is billed by those providers separately.

## Alternative: single Compute Engine VM

For the cheapest always-on setup (~$15/month on `e2-small`), skip Cloud Run:
create a VM with the `job-agent-sa` service account, install Docker, run the
backend container + `cloud-sql-proxy` (or fill in the root `docker-compose.yml`
with the two images above), put nginx or Caddy in front for TLS, and point a
domain at the static IP. Everything in steps 1–5 and 7 (images, secrets, IAM)
still applies; you lose managed autoscaling/revisions but none of the app's
constraints bite (it wants a single long-lived process anyway).

## Hardening ideas

- Move `crew_runner.progress_store` into the `jobs` table (or Redis /
  Memorystore) and run the pipeline via Cloud Run **Jobs** or Cloud Tasks —
  this removes the single-instance/single-worker constraint and makes deploys
  safe at any time.
- Restrict backend ingress: put both services behind a global HTTPS Load
  Balancer with a custom domain, set the API service to
  `--ingress=internal-and-cloud-load-balancing`.
- Replace the startup `create_all` with real Alembic migrations
  (see [migrations.md](migrations.md)).
- Pin image tags (never `latest`) and enable Artifact Registry vulnerability
  scanning.
- Add a Cloud SQL automated backup window and deletion protection.

---

## Deployment checklist

- [ ] GCP_Setup.md done (project, Cloud SQL, GCS bucket, `job-agent-sa`, OAuth client)
- [ ] Deployment APIs enabled (Run, Artifact Registry, Cloud Build, Secret Manager)
- [ ] Artifact Registry repo `job-agent` created
- [ ] `job-agent-sa` granted `roles/secretmanager.secretAccessor`
- [ ] All secrets created in Secret Manager (real values, not placeholders)
- [ ] `backend/Dockerfile` + `.dockerignore` filled in; image built & pushed
- [ ] `service-api.yaml` filled in (project ID, bucket, Weaviate URL, OAuth client ID)
- [ ] Backend deployed — `/api/health` returns healthy; tables auto-created
- [ ] `frontend/Dockerfile`, `nginx.conf`, `cloudbuild.yaml` added; image built with OAuth client ID
- [ ] Frontend deployed with `BACKEND_HOST` set
- [ ] `CORS_ORIGINS` updated to the frontend URL
- [ ] Frontend URL added to OAuth authorized JavaScript origins
- [ ] End-to-end test: register → upload resume → tailor job → SSE progress → downloads
