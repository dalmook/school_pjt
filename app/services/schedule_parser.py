from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from app.utils import parse_neis_date


ACADEMIC_KEYS = [
    "midterm",
    "finalterm",
    "mock_exam",
    "summer_vacation",
    "winter_vacation",
    "graduation_day",
    "school_anniversary",
    "discretionary_holidays",
    "other_events",
]


STATUS_PRIORITY = {
    "시험중": 7,
    "모의고사": 6,
    "방학중": 5,
    "재량휴업일": 4,
    "졸업식": 3,
    "행사일": 2,
    "정상수업": 1,
}


def _normalize_text(value: str) -> str:
    return (value or "").replace(" ", "").lower()


def classify_event(event_name: str) -> str:
    text = _normalize_text(event_name)

    if "중간고사" in text or ("중간" in text and ("고사" in text or "평가" in text)):
        return "midterm"
    if any(token in text for token in ("기말고사", "학기말고사")) or ("기말" in text and ("고사" in text or "평가" in text)):
        return "finalterm"
    if any(token in text for token in ("전국연합학력평가", "연합학력평가", "학력평가", "모의평가", "모의고사")):
        return "mock_exam"
    if any(token in text for token in ("여름방학", "하계방학")):
        return "summer_vacation"
    if any(token in text for token in ("겨울방학", "동계방학")):
        return "winter_vacation"
    if "졸업식" in text or ("졸업" in text and "식" in text):
        return "graduation_day"
    if "개교기념일" in text:
        return "school_anniversary"
    if any(token in text for token in ("학교장재량휴업일", "재량휴업일")):
        return "discretionary_holidays"
    if "종업식" in text:
        return "closing_ceremony"
    if "시업식" in text:
        return "opening_ceremony"
    if "입학식" in text:
        return "entrance_ceremony"
    if "방학식" in text:
        return "vacation_ceremony"
    return "other_events"


def normalize_period(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return f"{start_date.month}/{start_date.day}"
    if start_date.year == end_date.year:
        return f"{start_date.month}/{start_date.day}~{end_date.month}/{end_date.day}"
    return f"{start_date.isoformat()}~{end_date.isoformat()}"


def parse_schedule_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_dates: dict[tuple[str, str | None], list[date]] = defaultdict(list)
    for row in rows:
        target_date = parse_neis_date(row.get("AA_YMD"))
        event_name = str(row.get("EVENT_NM", "")).strip()
        details = str(row.get("EVENT_CNTNT", "")).strip() or None
        if not target_date or not event_name:
            continue
        grouped_dates[(event_name, details)].append(target_date)

    events: list[dict[str, Any]] = []
    for (event_name, details), dates in grouped_dates.items():
        sorted_dates = sorted(set(dates))
        start = sorted_dates[0]
        end = sorted_dates[0]
        for current in sorted_dates[1:]:
            if current == end + timedelta(days=1):
                end = current
                continue
            events.append(_to_event(event_name, details, start, end))
            start = current
            end = current
        events.append(_to_event(event_name, details, start, end))

    events.sort(key=lambda item: (item["start_date"], item["event_name"]))
    return events


def _to_event(event_name: str, details: str | None, start_date: date, end_date: date) -> dict[str, Any]:
    return {
        "event_name": event_name,
        "details": details,
        "category": classify_event(event_name),
        "start_date": start_date,
        "end_date": end_date,
        "period": normalize_period(start_date, end_date),
    }


def build_academic_summary(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {key: [] for key in ACADEMIC_KEYS}
    for event in events:
        category = event["category"]
        value = event["period"]
        if category == "other_events":
            summary[category].append(f"{value} {event['event_name']}")
            continue
        if category in summary:
            summary[category].append(value)
        elif category in {"closing_ceremony", "opening_ceremony", "entrance_ceremony", "vacation_ceremony"}:
            summary["other_events"].append(f"{value} {event['event_name']}")
        else:
            summary["other_events"].append(f"{value} {event['event_name']}")
    return summary


def compute_today_status(events: list[dict[str, Any]], target_date: date) -> str:
    active = [event for event in events if event["start_date"] <= target_date <= event["end_date"]]
    if not active:
        return "정상수업"

    statuses: list[str] = []
    for event in active:
        category = event["category"]
        if category in {"midterm", "finalterm"}:
            statuses.append("시험중")
        elif category == "mock_exam":
            statuses.append("모의고사")
        elif category in {"summer_vacation", "winter_vacation"}:
            statuses.append("방학중")
        elif category == "discretionary_holidays":
            statuses.append("재량휴업일")
        elif category == "graduation_day":
            statuses.append("졸업식")
        else:
            statuses.append("행사일")

    return max(statuses, key=lambda value: STATUS_PRIORITY[value])


def ongoing_events(events: list[dict[str, Any]], target_date: date) -> list[str]:
    rows = []
    for event in events:
        if event["start_date"] <= target_date <= event["end_date"]:
            rows.append(f"{event['period']} {event['event_name']}")
    return rows


def upcoming_events(events: list[dict[str, Any]], target_date: date, within_days: int = 14) -> list[str]:
    rows = []
    last_day = target_date + timedelta(days=within_days)
    for event in events:
        if target_date < event["start_date"] <= last_day:
            rows.append(f"{event['period']} {event['event_name']}")
    return rows
