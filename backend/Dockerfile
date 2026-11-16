# backend/Dockerfile — see docs/GCP_deployment.md §5
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm
WORKDIR /app
# Tectonic compiles the LaTeX resume to PDF. Install the release binary and
# pre-warm its package cache by compiling a resume once at build time, so
# runtime compiles never touch the network (TECTONIC_ONLY_CACHED=true).
ARG TECTONIC_VERSION=0.17.0
ENV TECTONIC_CACHE_DIR=/opt/tectonic-cache
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libfontconfig1 \
 && curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin tectonic \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Cold compile downloads the TeX bundle (~3 min) — the cache is baked into the image.
RUN python -c "import tempfile; from app.services.latex import TailoredResume, render_resume_latex; \
from app.services.latex.compiler import compile_latex_to_pdf; \
r = TailoredResume.model_validate({'personal_information': {'name': 'Warm Up', 'location': 'City', 'phone': '+1 555 0100', 'email': 'a@b.co', 'linkedin': 'https://linkedin.com/in/x', 'github': 'https://github.com/x'}, 'summary': '**warm**', 'experience': [{'job_title': 't', 'company': 'c', 'location': 'l', 'work_mode': 'Remote', 'start_date': 'JAN 2020', 'end_date': 'PRESENT', 'bullets': ['b']}], 'education': [{'degree': 'd', 'institution': 'i', 'gpa': '4.0', 'start_date': '2015', 'end_date': '2019'}], 'skills': [{'category': 'c', 'skills': ['s']}], 'projects': [{'name': 'p', 'url': 'https://x.io', 'bullets': ['b']}], 'publications': [{'citation': 'c'}], 'certifications': [{'name': 'n', 'issuer': 'i', 'year': '2024', 'url': 'https://x.io'}]}); \
res = compile_latex_to_pdf(render_resume_latex(r), tempfile.mkdtemp(), timeout=900); assert res.ok, res.error; print('tectonic cache warmed')" \
 && chmod -R a+rX /opt/tectonic-cache
ENV TECTONIC_ONLY_CACHED=true
# Cloud Run injects PORT (default 8080). Exactly ONE worker — see
# "runtime constraints" in docs/GCP_deployment.md: job progress lives in process memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
