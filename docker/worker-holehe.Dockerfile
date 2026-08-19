# Worker dedicated to Holehe. Same isolation as the other tool workers.
# Holehe (GPL-3.0) is invoked as a SUBPROCESS through app/plugins/holehe/driver.py:
# there is no code linkage with the rest of the platform.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Upstream: https://github.com/megadose/holehe (GPL-3.0)
# holehe pins its own httpx/bs4 versions: it is installed last so its constraints
# apply inside THIS container only.
ARG HOLEHE_VERSION=1.61
RUN pip install "holehe==${HOLEHE_VERSION}"

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10003 holeherun && chown -R holeherun:holeherun /app
USER holeherun

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--queues=holehe", "--concurrency=1", "--loglevel=INFO", "--hostname=holehe@%h"]
