from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services.region_service import RegionService


class DummyClient:
    async def search_schools(self, query: str):
        return []

    async def get_dataset_rows(self, dataset: str, params: dict):
        if dataset == "mealServiceDietInfo":
            return [
                {
                    "MLSV_YMD": "20260310",
                    "MMEAL_SC_CODE": "2",
                    "DDISH_NM": "카레(2.5.6)<br/>우유(2)<br/>김치",
                },
                {
                    "MLSV_YMD": "20260311",
                    "MMEAL_SC_CODE": "2",
                    "DDISH_NM": "볶음밥(5)<br/>국",
                },
            ]
        if dataset == "SchoolSchedule":
            return [
                {"AA_YMD": "20260310", "EVENT_NM": "중간고사"},
                {"AA_YMD": "20260720", "EVENT_NM": "여름방학"},
                {"AA_YMD": "20260721", "EVENT_NM": "여름방학"},
                {"AA_YMD": "20260816", "EVENT_NM": "개학행사"},
            ]
        return []


def _db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return TestingSession()


def test_region_create_and_register_school():
    db = _db_session()
    service = RegionService(db)

    region = service.create_region("병점", "행정동", "병점,진안동")
    assert region.region_name == "병점"

    saved = service.register_region_schools(
        region.id,
        [
            {
                "atpt_ofcdc_sc_code": "J10",
                "sd_schul_code": "7531038",
                "school_name": "병점중학교",
                "school_level": "중학교",
                "address": "경기도 화성시 병점동",
                "display_order": 0,
            }
        ],
    )
    assert len(saved) == 1
    assert saved[0].school_name == "병점중학교"


def test_schedule_classification_and_status_priority():
    db = _db_session()
    service = RegionService(db)

    merged = [
        {
            "event_name": "중간고사",
            "category": "midterm",
            "start_date": date(2026, 4, 21),
            "end_date": date(2026, 4, 23),
        },
        {
            "event_name": "여름방학",
            "category": "summer_vacation",
            "start_date": date(2026, 7, 20),
            "end_date": date(2026, 8, 14),
        },
        {
            "event_name": "체험학습",
            "category": "event",
            "start_date": date(2026, 4, 22),
            "end_date": date(2026, 4, 22),
        },
    ]

    assert service._classify_event("중간고사") == "midterm"
    assert service._classify_event("겨울방학") == "winter_vacation"
    assert service._today_status(merged, date(2026, 4, 22)) == "시험중"
    assert service._today_status(merged, date(2026, 7, 25)) == "방학중"
    assert service._today_status(merged, date(2026, 5, 1)) == "정상수업"


def test_region_overview_response_building():
    db = _db_session()
    service = RegionService(db)
    service.client = DummyClient()

    region = service.create_region("동탄")
    service.register_region_schools(
        region.id,
        [
            {
                "atpt_ofcdc_sc_code": "J10",
                "sd_schul_code": "7531038",
                "school_name": "동탄중학교",
                "school_level": "중학교",
                "address": "경기도 화성시 동탄",
                "display_order": 0,
            }
        ],
    )

    overview = asyncio.run(service.get_region_overview(region.id, date(2026, 3, 10)))

    assert overview["region"]["region_name"] == "동탄"
    assert overview["summary"]["school_count"] == 1
    assert overview["summary"]["meal_count"] == 1
    assert len(overview["rows"]) == 1
    assert overview["rows"][0]["today_status"] == "시험중"
    assert overview["rows"][0]["today_meal_summary"].startswith("카레")
