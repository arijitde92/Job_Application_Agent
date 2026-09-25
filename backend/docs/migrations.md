# Database Migrations (manual)

This project has **no Alembic**. Tables are auto-created on startup via
`Base.metadata.create_all` (`app/main.py`), which only creates **missing**
tables — it does **not** alter columns on tables that already exist.

When a model change alters an existing column, you must apply the change by
hand against the running MySQL database (e.g. Cloud SQL). Fresh databases pick
up the new schema automatically via `create_all`.

---

## 2026-06-18 — Make `jobs.github_profile_id` optional

**Why:** A GitHub profile is now optional when tailoring a resume. The column
was `NOT NULL` with `ON DELETE RESTRICT`; it becomes nullable with
`ON DELETE SET NULL` so that (a) jobs can be created without a GitHub profile,
and (b) deleting a GitHub profile leaves historical jobs intact (link cleared).

**Apply once to any existing (non-fresh) database:**

```sql
-- 1) Find the existing foreign-key constraint name on the column.
SELECT CONSTRAINT_NAME
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_NAME = 'jobs'
   AND COLUMN_NAME = 'github_profile_id'
   AND REFERENCED_TABLE_NAME IS NOT NULL;

-- 2) Drop the old RESTRICT FK, relax the column, re-add as SET NULL.
--    Replace <constraint_name> with the value from step 1 (often jobs_ibfk_2).
ALTER TABLE jobs DROP FOREIGN KEY <constraint_name>;
ALTER TABLE jobs MODIFY COLUMN github_profile_id INT NULL;
ALTER TABLE jobs
  ADD CONSTRAINT fk_jobs_github_profile
  FOREIGN KEY (github_profile_id) REFERENCES github_profiles(id)
  ON DELETE SET NULL;
```

**Fresh databases:** no action needed — `create_all` builds the `jobs` table
with the nullable column and `SET NULL` FK directly from the updated model.

---

## 2026-09-25 — Job description text as an alternative to a LinkedIn URL

**Why:** A job can now be created from a pasted or uploaded job description
instead of a LinkedIn URL (issue #10). `jobs.linkedin_job_url` becomes
nullable, and the new `jobs.job_description_input` column stores the text the
user supplied so a failed job can be retried. Exactly one of the two is set.

**Applied automatically on startup** by `app/main.py` (idempotent, like the
`parsed_resume_path` and tailored-artifact column migrations). If that step
logs "migration skipped", apply it by hand:

```sql
ALTER TABLE jobs MODIFY COLUMN linkedin_job_url VARCHAR(500) NULL;
ALTER TABLE jobs ADD COLUMN job_description_input TEXT NULL AFTER linkedin_job_url;
```

**Fresh databases:** no action needed — `create_all` builds both columns from
the updated model.
