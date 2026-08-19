# Worker dedicated to PhoneInfoga.
# The third-party binary stays in ITS OWN official container (service
# `phoneinfoga`): this worker only queries its REST API, or runs in local mode
# (validation plus search generation, no outbound request).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10004 phonerun && chown -R phonerun:phonerun /app
USER phonerun

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--queues=phoneinfoga,websearch", "--concurrency=2", "--loglevel=INFO", "--hostname=phone@%h"]
