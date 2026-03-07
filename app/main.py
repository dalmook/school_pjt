from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, get_settings
from app.db import Base, engine, ensure_sqlite_schema, session_scope
from app.routes.api import router as api_router
from app.routes.telegram import router as telegram_router
from app.routes.web import router as web_router
from app.services.notification_service import NotificationService
from app.utils import today_kst


settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


async def run_job(job_name: str, task):
    with session_scope() as db:
        service = NotificationService(db)
        try:
            result = await task(service)
            service.log_job(job_name, "success", str(result))
        except Exception as exc:
            logger.exception("Scheduler job failed: %s", job_name)
            service.log_job(job_name, "failed", str(exc))


async def cleanup_job(service: NotificationService) -> int:
    return service.cleanup_cache()


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(lambda: run_job("warm_long_term_cache", lambda service: service.warm_long_term_cache()), CronTrigger(hour=3, minute=30, timezone=settings.timezone), id="warm_long_term_cache", replace_existing=True)
    scheduler.add_job(lambda: run_job("prefetch", lambda service: service.prefetch_dates([today_kst(), today_kst() + timedelta(days=1)])), CronTrigger(hour=6, minute=30, timezone=settings.timezone), id="prefetch", replace_existing=True)
    scheduler.add_job(lambda: run_job("morning_custom", lambda service: service.send_today_briefs()), CronTrigger(minute="*", timezone=settings.timezone), id="morning_custom", replace_existing=True)
    scheduler.add_job(lambda: run_job("change_detect_18", lambda service: service.detect_timetable_changes([today_kst(), today_kst() + timedelta(days=1)])), CronTrigger(hour=18, minute=0, timezone=settings.timezone), id="change_detect_18", replace_existing=True)
    scheduler.add_job(lambda: run_job("evening_custom", lambda service: service.send_tomorrow_briefs()), CronTrigger(minute="*", timezone=settings.timezone), id="evening_custom", replace_existing=True)
    scheduler.add_job(lambda: run_job("change_detect_22", lambda service: service.detect_timetable_changes([today_kst(), today_kst() + timedelta(days=1)])), CronTrigger(hour=22, minute=0, timezone=settings.timezone), id="change_detect_22", replace_existing=True)
    scheduler.add_job(lambda: run_job("dday_alerts", lambda service: service.send_dday_alerts()), CronTrigger(hour=7, minute=5, timezone=settings.timezone), id="dday_alerts", replace_existing=True)
    scheduler.add_job(lambda: run_job("cleanup", cleanup_job), CronTrigger(hour=23, minute=0, timezone=settings.timezone), id="cleanup", replace_existing=True)
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema()
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
app.include_router(web_router)
app.include_router(api_router)
app.include_router(telegram_router)
