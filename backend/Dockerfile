# Backend Dockerfile — Collecct FastAPI API + Celery (worker + beat)

FROM python:3.11-slim

WORKDIR /app

# System deps:
#  - curl: healthcheck
#  - build-essential: any sdist that needs compiling during uv sync
#  - libgomp1 / libgl1 / libglib2.0-0 / libsm6 / libxext6 / libxrender1:
#    native libs LiteParse (PDFium + OCR) and numpy/matplotlib commonly need.
#  - libreoffice-*: the docx/pptx agent skills shell out to `soffice` for PDF
#    conversion / thumbnails (agent/skills/*/scripts/office/soffice.py).
#  - tini: PID 1 (see ENTRYPOINT below).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    build-essential \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libreoffice-core \
    libreoffice-writer \
    libreoffice-impress \
    libreoffice-calc \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies from the lockfile first (better layer caching).
# --no-install-project: install ONLY deps at this layer (the app code isn't copied yet,
# and the project runs from the working dir — it's not a pip-installed package).
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

# Application code
COPY . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/api/health" || exit 1

# start.sh dispatches on $SERVICE_ROLE (web | worker | beat | worker-beat | all).
# Default `all` runs the API + Celery worker + Celery beat in ONE container.
# To split into separate services (recommended at scale), set SERVICE_ROLE per service
# and run beat as EXACTLY ONE instance (a second beat duplicates every scheduled job).
#
# tini runs as PID 1 and reaps zombie processes. Every LibreOffice start spawns gpgconf, gpg and
# gpgsm to look for signing certificates; when soffice exits they are re-parented to PID 1, and
# uvicorn (PID 1 before this) never collects them. Each zombie keeps a slot in the container's
# task limit (pids.max = 1000 on Railway): production measured 10 after 8 minutes, and after a day
# or two of library analysis the container had no slots left and every request failed with
# "RuntimeError: can't start new thread". -g sends Railway's stop signal to start.sh's whole
# process group, so the worker and beat shut down cleanly too, not just the API.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["sh", "start.sh"]
