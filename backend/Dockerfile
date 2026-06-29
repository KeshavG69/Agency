# Backend Dockerfile — Collecct FastAPI API + Celery (worker + beat)

FROM python:3.11-slim

WORKDIR /app

# System deps:
#  - curl: healthcheck
#  - build-essential: any sdist that needs compiling during uv sync
#  - libgomp1 / libgl1 / libglib2.0-0 / libsm6 / libxext6 / libxrender1:
#    native libs LiteParse (PDFium + OCR) and numpy/matplotlib commonly need.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies from the lockfile first (better layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

# Application code
COPY . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# start.sh dispatches on $SERVICE_ROLE (web | worker | beat | worker-beat | all).
# Default `all` runs the API + Celery worker + Celery beat in ONE container.
# To split into separate services (recommended at scale), set SERVICE_ROLE per service
# and run beat as EXACTLY ONE instance (a second beat duplicates every scheduled job).
CMD ["sh", "start.sh"]
