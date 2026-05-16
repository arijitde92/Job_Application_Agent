# Google Cloud Platform (GCP) Setup Guide

This guide details the steps required to configure Google Cloud Platform (GCP) for the **Job Application Agent** project. The project relies on **Google BigQuery** as a vector database to store and search document embeddings and **Vertex AI** for generating semantic embeddings.

---

## 1. Prerequisites
- A Google Cloud account. If you do not have one, sign up at [cloud.google.com](https://cloud.google.com/).
- A billing account linked to your GCP account (BigQuery and Vertex AI require an active billing account, though they offer free tiers/credits).

## 2. Create a Google Cloud Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Click on the project drop-down menu at the top of the page.
3. Click **New Project**.
4. Enter a project name (e.g., `job-application-agent`).
5. Note your **Project ID** (e.g., `inbound-byway-457408-c9` from the README, though yours will be unique).
6. Click **Create** and ensure your new project is selected in the console.

## 3. Enable Required APIs
You need to enable both the Vertex AI and BigQuery APIs for your project.

1. Navigate to **APIs & Services > Library** in the left sidebar.
2. Search for **Agent Platform API** (formerly Vertex AI API).
3. Click on the result and click **Enable**.
4. Search for **BigQuery API**.
5. Click on the result and click **Enable**.

## 4. Set Up BigQuery Dataset
The system will need a BigQuery dataset to store tables containing your GitHub repository data and their vector embeddings.

1. Navigate to **BigQuery** from the GCP Console sidebar.
2. In the Explorer pane, click the three dots (`⋮`) next to your Project ID and select **Create dataset**.
3. Fill in the dataset details:
   - **Dataset ID:** `job_applier_app` (or your preferred name, matching `GCP_DATASET_NAME` in `.env`).
   - **Data location:** `asia-south2` (or your preferred region, matching `GCP_LOCATION` in `.env`).
4. Leave other settings as default and click **Create dataset**.
*(Note: The table `github_repo_data` will typically be created automatically by the LangChain/BigQuery vector store integration when you run the application, provided the dataset exists.)*

## 5. Create a Service Account and Generate JSON Key
To allow your local Python script to interact with GCP securely, you must use a Service Account.

1. Navigate to **IAM & Admin > Service Accounts** in the GCP Console.
2. Click **Create Service Account** at the top.
3. Provide a name (e.g., `job-agent-sa`) and a description. Click **Create and Continue**.
4. **Grant Access (Permissions):** You must assign the following roles to the service account so it can manage BigQuery data and call Vertex AI models:
   - **BigQuery Admin** (or `BigQuery Data Editor` + `BigQuery Job User` for least privilege)
   - **Vertex AI User**
5. Click **Continue** and then **Done**.
6. Find the newly created service account in the list, click the three dots (`⋮`) under the Actions column, and select **Manage keys**.
7. Click **Add Key > Create new key**.
8. Select **JSON** and click **Create**.
9. The JSON file will automatically download to your computer. Store this file securely and **DO NOT** commit it to version control (e.g., GitHub).

## 6. Configure Environment Variables
Finally, update your local project to use the GCP resources and credentials you just created.

Create or update your `.env` file in the root of the `Job_Application_Agent` directory:

```env
# Path to the downloaded Service Account JSON key
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your/gcp-service-account.json

# Your GCP Project ID
GCP_PROJECT_ID=your-unique-project-id

# BigQuery Dataset Name
GCP_DATASET_NAME=job_applier_app

# GCP Region (must match your dataset location)
GCP_LOCATION=asia-south2

# BigQuery Table Name (will be created automatically if it doesn't exist)
GCP_TABLE_NAME=github_repo_data

# Vertex AI Model for Embeddings
VERTEX_AI_MODEL=text-embedding-005
```

## Summary Checklist
- [ ] GCP Project Created
- [ ] Vertex AI API Enabled
- [ ] BigQuery API Enabled
- [ ] BigQuery Dataset Created (`job_applier_app`)
- [ ] Service Account Created with roles (`BigQuery Admin`, `Vertex AI User`)
- [ ] Service Account JSON key downloaded
- [ ] `.env` file updated with valid GCP configuration
