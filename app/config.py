from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "NEIS School Alert")
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    app_secret: str = os.getenv("APP_SECRET", "change-me")
    neis_api_key: str = os.getenv("NEIS_API_KEY", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_bot_username: str = os.getenv("TELEGRAM_BOT_USERNAME", "")
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'school_alert.db'}")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
    timezone: str = os.getenv("TZ", "Asia/Seoul")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    cache_ttl_minutes: int = int(os.getenv("CACHE_TTL_MINUTES", "180"))
    schedule_cache_hours: int = int(os.getenv("SCHEDULE_CACHE_HOURS", "30"))
    school_info_cache_hours: int = int(os.getenv("SCHOOL_INFO_CACHE_HOURS", "168"))
    class_info_cache_hours: int = int(os.getenv("CLASS_INFO_CACHE_HOURS", "24"))
    meal_cache_hours: int = int(os.getenv("MEAL_CACHE_HOURS", "18"))
    timetable_cache_hours: int = int(os.getenv("TIMETABLE_CACHE_HOURS", "8"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
    request_max_retries: int = int(os.getenv("REQUEST_MAX_RETRIES", "3"))
    morning_brief_default: str = os.getenv("MORNING_BRIEF_DEFAULT", "07:00")
    evening_brief_default: str = os.getenv("EVENING_BRIEF_DEFAULT", "21:00")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "telegram-secret")
    admin_token: str = os.getenv("ADMIN_TOKEN", "admin-token")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
