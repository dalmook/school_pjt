from __future__ import annotations

from datetime import date

from app.services.schedule_parser import (
    build_academic_summary,
    classify_event,
    compute_today_status,
    parse_schedule_rows,
    upcoming_events,
)


def test_schedule_classification_categories():
    assert classify_event("1학기 중간고사") == "midterm"
    assert classify_event("2학기 기말고사") == "finalterm"
    assert classify_event("전국연합학력평가") == "mock_exam"
    assert classify_event("하계방학") == "summer_vacation"
    assert classify_event("동계방학") == "winter_vacation"
    assert classify_event("제30회 졸업식") == "graduation_day"
    assert classify_event("개교기념일") == "school_anniversary"
    assert classify_event("학교장재량휴업일") == "discretionary_holidays"


def test_today_status_priority():
    events = [
        {
            "event_name": "체험학습",
            "category": "other_events",
            "start_date": date(2026, 4, 21),
            "end_date": date(2026, 4, 21),
            "period": "4/21",
            "details": None,
        },
        {
            "event_name": "모의고사",
            "category": "mock_exam",
            "start_date": date(2026, 4, 21),
            "end_date": date(2026, 4, 21),
            "period": "4/21",
            "details": None,
        },
        {
            "event_name": "중간고사",
            "category": "midterm",
            "start_date": date(2026, 4, 21),
            "end_date": date(2026, 4, 23),
            "period": "4/21~4/23",
            "details": None,
        },
    ]
    assert compute_today_status(events, date(2026, 4, 21)) == "시험중"


def test_parse_and_summary_and_upcoming():
    rows = [
        {"AA_YMD": "20260421", "EVENT_NM": "중간고사"},
        {"AA_YMD": "20260422", "EVENT_NM": "중간고사"},
        {"AA_YMD": "20260625", "EVENT_NM": "기말고사"},
        {"AA_YMD": "20260326", "EVENT_NM": "모의평가"},
        {"AA_YMD": "20260720", "EVENT_NM": "여름방학"},
        {"AA_YMD": "20270106", "EVENT_NM": "졸업식"},
        {"AA_YMD": "20260504", "EVENT_NM": "재량휴업일"},
    ]

    events = parse_schedule_rows(rows)
    summary = build_academic_summary(events)

    assert summary["midterm"]
    assert summary["finalterm"]
    assert summary["mock_exam"]
    assert summary["summer_vacation"]
    assert summary["graduation_day"]
    assert summary["discretionary_holidays"]

    ups = upcoming_events(events, date(2026, 3, 20), within_days=14)
    assert any("모의평가" in item for item in ups)
