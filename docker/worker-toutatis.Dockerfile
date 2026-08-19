# Worker dedicated to Toutatis (OPTIONAL).
# This service only starts under the `toutatis` Compose profile:
#   docker compose --profile toutatis up -d
# The session cookie is passed at runtime over STDIN; it is never written to disk
# nor present in the container environment.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Upstream: https://github.com/megadose/toutatis (GPL-3.0)
ARG TOUTATIS_VERSION=1.4
RUN pip install "toutatis==${TOUTATIS_VERSION}"

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10005 toutatisrun && chown -R toutatisrun:toutatisrun /app
USER toutatisrun

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--queues=toutatis", "--concurrency=1", "--loglevel=INFO", "--hostname=toutatis@%h"]
