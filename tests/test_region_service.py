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
                }
            ]
        if dataset == "SchoolSchedule":
            return [
                {"AA_YMD": "20260310", "EVENT_NM": "중간고사"},
                {"AA_YMD": "20260326", "EVENT_NM": "전국연합학력평가"},
                {"AA_YMD": "20260720", "EVENT_NM": "하계방학"},
                {"AA_YMD": "20260721", "EVENT_NM": "하계방학"},
                {"AA_YMD": "20270106", "EVENT_NM": "졸업식"},
                {"AA_YMD": "20260504", "EVENT_NM": "학교장재량휴업일"},
            ]
        if dataset == "schoolInfo":
            return [
                {
                    "SCHUL_NM": "동탄중학교",
                    "SCHUL_KND_SC_NM": "중학교",
                    "ATPT_OFCDC_SC_NM": "경기도교육청",
                    "ORG_RDNMA": "경기도 화성시",
                    "ORG_RDNDA": "동탄순환대로 1",
                    "ORG_TELNO": "031-000-0000",
                    "HMPG_ADRES": "https://example.sch.kr",
                }
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

    region = service.create_region("병점")
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

    assert overview["summary"]["school_count"] == 1
    assert overview["summary"]["exam_count"] == 1
    assert overview["summary"]["mock_exam_count"] == 1
    assert overview["summary"]["vacation_count"] == 1

    row = overview["rows"][0]
    assert row["school_name"] == "동탄중학교"
    assert row["today_status"] == "시험중"
    assert row["academic_summary"]["midterm"]
    assert row["academic_summary"]["mock_exam"]
    assert row["academic_summary"]["summer_vacation"]
    assert row["academic_summary"]["graduation_day"]
    assert row["academic_summary"]["discretionary_holidays"]
    assert row["today_meal_summary"].startswith("카레")


def test_inactive_school_reactivation():
    db = _db_session()
    service = RegionService(db)
    region = service.create_region("봉담")
    saved = service.register_region_schools(
        region.id,
        [
            {
                "atpt_ofcdc_sc_code": "J10",
                "sd_schul_code": "7012345",
                "school_name": "봉담중학교",
                "school_level": "중학교",
                "address": "경기도 화성시 봉담",
                "display_order": 0,
            }
        ],
    )
    assert len(saved) == 1
    school_id = saved[0].id

    assert service.deactivate_region_school(region.id, school_id) is True
    assert len(service.get_region_schools(region.id, only_active=True)) == 0

    service.register_region_schools(
        region.id,
        [
            {
                "atpt_ofcdc_sc_code": "J10",
                "sd_schul_code": "7012345",
                "school_name": "봉담중학교",
                "school_level": "중학교",
                "address": "경기도 화성시 봉담",
                "display_order": 0,
            }
        ],
    )
    active_rows = service.get_region_schools(region.id, only_active=True)
    assert len(active_rows) == 1
    assert active_rows[0].id == school_id
