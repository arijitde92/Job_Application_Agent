# Google Cloud Platform (GCP) Setup Guide

This guide details the steps required to configure Google Cloud Platform (GCP) for the **Job Application Agent** project. The project relies on:

- **Cloud SQL for MySQL** — relational database for user accounts, resumes, and job records
- **Cloud Storage (GCS)** — file storage for uploaded and tailored resumes

> **The GitHub RAG pipeline no longer uses GCP.** Repo embeddings moved from
> BigQuery + Vertex `text-embedding-005` to **Weaviate Cloud** + **Voyage AI**
> (`voyage-code-3` embeddings, `rerank-2.5-lite` reranking). No BigQuery
> dataset, BigQuery API, or Vertex AI access is required. Configure
> `WEAVIATE_URL` / `WEAVIATE_API_KEY` / `VOYAGE_API_KEY` instead — see
> `.env.example`.

---

## 1. Prerequisites
- A Google Cloud account. If you do not have one, sign up at [cloud.google.com](https://cloud.google.com/).
- A billing account linked to your GCP account (Cloud SQL and Cloud Storage require an active billing account, though they offer free tiers/credits).
- `gcloud` CLI installed and authenticated: `gcloud auth login`

---

## 2. Create a Google Cloud Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Click on the project drop-down menu at the top of the page.
3. Click **New Project**.
4. Enter a project name (e.g., `job-application-agent`).
5. Note your **Project ID** — you'll need this in your `.env` file.
6. Click **Create** and ensure your new project is selected in the console.

---

## 3. Enable Required APIs
Navigate to **APIs & Services > Library** and enable all of the following:

| API | Purpose |
|-----|---------|
| **Cloud SQL Admin API** | Managed MySQL database |
| **Cloud Storage API** | Resume file storage |

```bash
# Enable all required APIs via CLI
gcloud services enable sqladmin.googleapis.com \
    storage.googleapis.com \
    --project=YOUR_PROJECT_ID
```

---

## 4. Vector Store (no GCP resources needed)

GitHub repo embeddings live in **Weaviate Cloud**, not BigQuery. Create a free
sandbox cluster at [console.weaviate.cloud](https://console.weaviate.cloud/),
copy its REST endpoint and an API key into `WEAVIATE_URL` / `WEAVIATE_API_KEY`,
and get a `VOYAGE_API_KEY` from [voyageai.com](https://www.voyageai.com/).

The collection (`GithubRepoData`) is created automatically on first ingestion —
there is nothing to provision by hand.

---

## 5. Create a Cloud SQL Instance (MySQL)

This is the relational database storing user accounts, resume metadata, and job records.

### 5a. Create the instance

```bash
gcloud sql instances create job-applier-mysql \
  --database-version=MYSQL_8_0 \
  --tier=db-f1-micro \
  --region=asia-south2 \
  --root-password=YOUR_SECURE_ROOT_PASSWORD \
  --storage-type=SSD \
  --storage-size=10GB \
  --project=YOUR_PROJECT_ID
```

> **Cost:** `db-f1-micro` costs approximately $7/month.

Windows PowerShell equivalent:

```powershell
gcloud sql instances create job-applier-mysql `
  --database-version=MYSQL_8_0 `
  --tier=db-f1-micro `
  --region=asia-south2 `
  --root-password=YOUR_SECURE_ROOT_PASSWORD `
  --storage-type=SSD `
  --storage-size=10GB `
  --project=YOUR_PROJECT_ID
```

### 5b. Create the application database

```bash
gcloud sql databases create job_applier \
  --instance=job-applier-mysql \
  --project=YOUR_PROJECT_ID
```

Windows PowerShell equivalent:

```powershell
gcloud sql databases create job_applier `
  --instance=job-applier-mysql `
  --project=YOUR_PROJECT_ID
```

### 5c. Connect with the Cloud SQL Auth Proxy (recommended for local development)

The Auth Proxy avoids authorizing your changing public IP. It makes an
authenticated, encrypted connection to Cloud SQL and exposes it locally on
`127.0.0.1:3306`. You do **not** need to add an authorized network for this
connection. Keep the instance's public IP enabled for the proxy's default
connectivity.

1. **Grant proxy access.** Complete [Step 7](#7-create-a-service-account-and-generate-json-key)
   and use the `job-agent-sa` key configured in `GOOGLE_APPLICATION_CREDENTIALS`.
   The account needs the **Cloud SQL Client** role (granted in Step 7).
2. **Install the proxy.** Download the latest Cloud SQL Auth Proxy v2 binary
   for your OS and architecture from the [Cloud SQL Auth Proxy installation
   guide](https://cloud.google.com/sql/docs/mysql/connect-auth-proxy#install).
   On Ubuntu, make it executable and put it on your `PATH`; on Windows, put
   `cloud-sql-proxy.exe` on your `PATH` or note its full path.

   Ubuntu (after downloading the Linux AMD64 binary to `~/Downloads`):

   ```bash
   sudo install -m 755 ~/Downloads/cloud-sql-proxy /usr/local/bin/cloud-sql-proxy
   cloud-sql-proxy --version
   ```

   Windows PowerShell (after downloading the Windows AMD64 binary):

   ```powershell
   $proxyDir = "$HOME\cloud-sql-proxy"
   New-Item -ItemType Directory -Force -Path $proxyDir | Out-Null
   Move-Item "$HOME\Downloads\cloud-sql-proxy.exe" "$proxyDir\cloud-sql-proxy.exe"
   $env:PATH = "$proxyDir;$env:PATH"
   cloud-sql-proxy.exe --version
   ```

   The PowerShell `PATH` change applies to the current terminal. Set it again
   in any new terminal, or add the folder to your user `PATH` permanently.
3. **Configure the backend** in `backend/.env`. Use the credential path format
   for your OS:

   ```env
   GCP_PROJECT_ID=YOUR_PROJECT_ID
   GCP_LOCATION=asia-south2
   MYSQL_HOST=127.0.0.1
   MYSQL_PORT=3306
   MYSQL_USER=root
   MYSQL_PASSWORD=YOUR_SECURE_ROOT_PASSWORD
   MYSQL_DATABASE=job_applier
   ```

   Ubuntu example:

   ```env
   GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
   ```

   Windows PowerShell example:

   ```env
   GOOGLE_APPLICATION_CREDENTIALS=C:\absolute\path\to\service-account.json
   ```

4. **Start the app with the proxy.** From the `backend` directory, run the
   same command in Ubuntu or Windows PowerShell:

   ```bash
   uv run dev
   ```

   This starts the proxy, API, and frontend together. Alternatively, run
   `uv run db-proxy` in one terminal and `uv run api` in another. If the
   executable is not on `PATH`, set its path before starting either command.
   On Ubuntu:

   ```bash
   export CLOUD_SQL_PROXY_BIN=/path/to/cloud-sql-proxy
   ```

   On Windows PowerShell:

   ```powershell
   $env:CLOUD_SQL_PROXY_BIN = "C:\path\to\cloud-sql-proxy.exe"
   ```

   The project builds the instance connection name from `GCP_PROJECT_ID`,
   `GCP_LOCATION`, and `job-applier-mysql`. Confirm the name in either shell:

   ```bash
   gcloud sql instances describe job-applier-mysql \
     --project=YOUR_PROJECT_ID \
     --format="value(connectionName)"
   ```

   PowerShell:

   ```powershell
   gcloud sql instances describe job-applier-mysql `
     --project=YOUR_PROJECT_ID `
     --format="value(connectionName)"
   ```

### 5d. Alternative: authorize your current IP for a direct connection

You can connect directly without running the proxy, but this requires a static
public IP or updating Cloud SQL's authorized networks whenever your public IP
changes. Do not authorize `0.0.0.0/0`.

1. Get your current public IPv4 address.

   Ubuntu:

   ```bash
   curl -4 -s https://api.ipify.org
   ```

   Windows PowerShell:

   ```powershell
   (Invoke-RestMethod -Uri "https://api.ipify.org")
   ```

2. In Google Cloud Console, open **Cloud SQL → Instances →
   job-applier-mysql → Connections → Networking**, add the address with `/32`
   (for example, `203.0.113.10/32`), and save. This avoids replacing any other
   authorized networks already configured on the instance.
3. Set `MYSQL_HOST` in `backend/.env` to the instance's public IP address.
   Find it in **Cloud SQL → Instances → job-applier-mysql → Overview** or run
   the command for your shell.

   Ubuntu:

   ```bash
   gcloud sql instances describe job-applier-mysql \
     --project=YOUR_PROJECT_ID \
     --format="value(ipAddresses[0].ipAddress)"
   ```

   Windows PowerShell:

   ```powershell
   gcloud sql instances describe job-applier-mysql `
     --project=YOUR_PROJECT_ID `
     --format="value(ipAddresses[0].ipAddress)"
   ```

   Set `MYSQL_PORT=3306`. Start the API normally; do not start the proxy. If
   your public IP changes, update the authorized network before reconnecting.

> **Note:** Database tables (`users`, `github_profiles`, `resumes`, `jobs`) are created automatically on first backend startup.

---

## 6. Create a GCS Bucket

This bucket stores uploaded and tailored resume files.

Use the current `gcloud storage` command to create the bucket and enable
uniform bucket-level access in one step.

Ubuntu:

```bash
gcloud storage buckets create gs://job-applier-YOUR_PROJECT_ID-resumes \
  --project=YOUR_PROJECT_ID \
  --location=asia-south2 \
  --uniform-bucket-level-access
```

Windows PowerShell:

```powershell
gcloud storage buckets create gs://job-applier-YOUR_PROJECT_ID-resumes `
  --project=YOUR_PROJECT_ID `
  --location=asia-south2 `
  --uniform-bucket-level-access
```

Replace `YOUR_PROJECT_ID` in both places with your actual GCP project ID.
Bucket names must be globally unique. `--location` sets the bucket's location,
and uniform bucket-level access is enabled at creation; no separate `gsutil`
command is needed.

---

## 7. Create a Service Account and Generate JSON Key

```bash
# Create the service account
gcloud iam service-accounts create job-agent-sa \
  --description="Job Application Agent Service Account" \
  --display-name="Job Agent SA" \
  --project=YOUR_PROJECT_ID

# Assign required roles
SA_EMAIL="job-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" --role="roles/cloudsql.client"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" --role="roles/storage.objectAdmin"

# Download the JSON key
gcloud iam service-accounts keys create ./service-account.json \
  --iam-account=$SA_EMAIL \
  --project=YOUR_PROJECT_ID
```

Windows PowerShell equivalent:

```powershell
# Create the service account
gcloud iam service-accounts create job-agent-sa `
  --description="Job Application Agent Service Account" `
  --display-name="Job Agent SA" `
  --project=YOUR_PROJECT_ID

# Assign required roles
$SA_EMAIL = "job-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/cloudsql.client"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/storage.objectAdmin"

# Download the JSON key to the current directory
gcloud iam service-accounts keys create .\service-account.json `
  --iam-account="$SA_EMAIL" `
  --project=YOUR_PROJECT_ID
```

> **Security:** Store `service-account.json` securely and **never commit it to version control**. It is already listed in `.gitignore`.

---

## 8. Set Up Google OAuth (Optional — for "Sign in with Google")

1. Go to [GCP Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. Click **Create Credentials → OAuth 2.0 Client ID**.
3. If prompted, configure the **OAuth consent screen** first (External, fill in app name).
4. **Application type:** Web application.
5. **Authorized JavaScript origins:** `http://localhost:5173` (and your production domain).
6. **Authorized redirect URIs:** `http://localhost:5173`.
7. Click **Create** and copy the **Client ID** and **Client Secret**.

---

## 9. Configure Environment Variables

Update `backend/.env` with the values for your environment:

```env
# ── GCP Core ──────────────────────────────────────────────────────
# Path to the downloaded Service Account JSON key.
# Ubuntu: /absolute/path/to/service-account.json
# Windows: C:\absolute\path\to\service-account.json
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json

# Your GCP Project ID
GCP_PROJECT_ID=your-unique-project-id

GCP_LOCATION=asia-south2

# ── Vector store (Weaviate Cloud + Voyage AI, not GCP) ─────────────
WEAVIATE_URL=your-cluster.c0.region.gcp.weaviate.cloud
WEAVIATE_API_KEY=YOUR_WEAVIATE_API_KEY
WEAVIATE_COLLECTION_NAME=GithubRepoData
VOYAGE_API_KEY=YOUR_VOYAGE_API_KEY
VOYAGE_EMBED_MODEL=voyage-code-3
VOYAGE_EMBED_DIMENSION=1024
VOYAGE_RERANK_MODEL=rerank-2.5-lite

# ── Cloud SQL MySQL ────────────────────────────────────────────────
MYSQL_HOST=127.0.0.1          # Use 127.0.0.1 when Cloud SQL Auth Proxy is running
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=YOUR_SECURE_ROOT_PASSWORD
MYSQL_DATABASE=job_applier

# ── Cloud Storage ──────────────────────────────────────────────────
GCS_BUCKET_NAME=job-applier-your-project-id-resumes

# ── JWT Auth ───────────────────────────────────────────────────────
# Generate a strong random key: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET_KEY=CHANGE_ME_TO_A_RANDOM_SECRET
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=1440

# ── Google OAuth (optional) ────────────────────────────────────────
GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret

# ── Existing API Keys ──────────────────────────────────────────────
BRIGHT_DATA_API_KEY=...
GITHUB_PERSONAL_ACCESS_TOKEN=...
GEMINI_API_KEY=...
SERPER_API_KEY=...
```

---

## Summary Checklist

### Existing (CLI pipeline)
- [ ] GCP Project Created
- [ ] Weaviate Cloud cluster created → `WEAVIATE_URL` / `WEAVIATE_API_KEY` set
- [ ] Voyage AI API key obtained → `VOYAGE_API_KEY` set
- [ ] Service Account JSON key downloaded → path set in `GOOGLE_APPLICATION_CREDENTIALS`

### New (Web App)
- [ ] Cloud SQL Admin API Enabled
- [ ] Cloud Storage API Enabled
- [ ] Cloud SQL instance created (`job-applier-mysql`, `db-f1-micro`, `asia-south2`)
- [ ] MySQL database created (`job_applier`)
- [ ] Choose a local Cloud SQL connection method:
  - [ ] Cloud SQL Auth Proxy installed and configured (recommended; no authorized network needed)
  - [ ] Current public IPv4 authorized with `/32` and `MYSQL_HOST` set to the instance's public IP
- [ ] GCS bucket created (`job-applier-YOUR_PROJECT_ID-resumes`)
- [ ] Service Account roles added: `Cloud SQL Client`, `Storage Object Admin`
- [ ] `backend/.env` updated with `MYSQL_*`, `GCS_BUCKET_NAME`, `JWT_SECRET_KEY`
- [ ] Google OAuth credentials created (optional, for Google Sign-In)
