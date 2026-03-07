from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings


ALLERGY_CODE_MAP = {
    "1": "난류",
    "2": "우유",
    "3": "메밀",
    "4": "땅콩",
    "5": "대두",
    "6": "밀",
    "7": "고등어",
    "8": "게",
    "9": "새우",
    "10": "돼지고기",
    "11": "복숭아",
    "12": "토마토",
    "13": "아황산류",
    "14": "호두",
    "15": "닭고기",
    "16": "쇠고기",
    "17": "오징어",
    "18": "조개류",
    "19": "잣",
}


def now_kst() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


def today_kst() -> date:
    return now_kst().date()


def as_kst_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def stable_hash(payload: object) -> str:
    dumped = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def new_web_key() -> str:
    return secrets.token_hex(16)


def parse_neis_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def daterange(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def school_year_for_date(target_date: date) -> int:
    return target_date.year if target_date.month >= 3 else target_date.year - 1


def school_year_range(target_date: date) -> tuple[int, date, date]:
    school_year = school_year_for_date(target_date)
    start = date(school_year, 3, 1)
    end = date(school_year + 1, 2, 28)
    return school_year, start, end


def blocked_timetable_period(target_date: date) -> bool:
    return date(2023, 8, 1) <= target_date < date(2025, 3, 1)


def to_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False)


def from_json(data: str) -> object:
    return json.loads(data)
