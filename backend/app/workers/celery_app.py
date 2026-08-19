"""Celery application.

Every third-party tool gets ITS OWN queue and ITS OWN worker container:
  default      -> orchestration and persistence (the only worker with DB access)
  sherlock     -> worker-sherlock
  holehe       -> worker-holehe
  phoneinfoga  -> worker-phoneinfoga
  toutatis     -> worker-toutatis (optional)
  websearch    -> worker-websearch

Tool workers only ever receive execution tasks: they open no PostgreSQL
connection and mount no host volume.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()

celery_app = Celery(
    "reconcore",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=60 * 60 * 24,
    task_soft_time_limit=settings.plugin_default_timeout + 60,
    task_time_limit=settings.plugin_default_timeout + 120,
    task_default_queue="default",
    task_routes={
        "reconcore.plugin_execute": {"queue": "default"},  # overridden per call
        "reconcore.persist_plugin_result": {"queue": "default"},
        "reconcore.start_search": {"queue": "default"},
        "reconcore.health_check": {"queue": "default"},
        "reconcore.apply_retention": {"queue": "default"},
    },
    beat_schedule={
        "plugin-health-hourly": {
            "task": "reconcore.health_check",
            "schedule": crontab(minute=0),
        },
        "retention-daily": {
            "task": "reconcore.apply_retention",
            "schedule": crontab(hour=3, minute=30),
        },
    },
)
