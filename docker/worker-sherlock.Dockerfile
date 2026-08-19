# Worker dedicated to Sherlock.
# Isolation: this container has NO PostgreSQL access (see docker-compose.yml,
# `bus` network only), mounts no host volume, and holds no application secret.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Third-party tool, pinned to a reviewed version.
# Upstream: https://github.com/sherlock-project/sherlock (MIT)
# Audit before any version bump: `osint plugin audit sherlock`.
ARG SHERLOCK_VERSION=0.15.0
RUN pip install "sherlock-project==${SHERLOCK_VERSION}"

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10002 sherlockrun && chown -R sherlockrun:sherlockrun /app
USER sherlockrun

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--queues=sherlock", "--concurrency=1", "--loglevel=INFO", "--hostname=sherlock@%h"]
