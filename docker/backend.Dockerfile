# Shared base image: FastAPI API plus the orchestration worker.
# These two services are the ONLY ones with PostgreSQL access.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is required by the healthchecks; no OSINT tool is installed here.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-deps -e .

# Unprivileged user: no service runs as root.
RUN useradd --create-home --uid 10001 reconcore \
 && chown -R reconcore:reconcore /app
USER reconcore

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
