import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR

log = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_MINUTES", "30"))


def _on_job_error(event) -> None:
    log.error("Scheduler job %s raised: %s", event.job_id, event.exception)


def init_scheduler(app) -> BackgroundScheduler:
    from app.feeds import poll_all_feeds
    from app.pipeline import run_pipeline, regenerate_preferences

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        poll_all_feeds,
        "interval",
        minutes=POLL_INTERVAL,
        id="poll_feeds",
        args=[app],
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_pipeline,
        "interval",
        minutes=POLL_INTERVAL,
        id="run_pipeline",
        args=[app],
        start_date="2000-01-01 00:00:31",  # 31-second offset so it fires after poll
        misfire_grace_time=600,
    )
    scheduler.add_job(
        regenerate_preferences,
        "cron",
        hour=2,
        minute=0,
        id="regen_prefs",
        args=[app],
        misfire_grace_time=1800,
    )

    return scheduler
