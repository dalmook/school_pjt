from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RegionGroup, RegionSchool
from app.schemas import RegionSchoolRegisterItem
from app.services.meal_service import MealService
from app.services.neis_client import NeisClient
from app.services.schedule_parser import (
    build_academic_summary,
    compute_today_status,
    ongoing_events,
    parse_schedule_rows,
    upcoming_events,
)
from app.utils import parse_neis_date, school_year_range


class RegionService:
    def __init__(self, db: Session):
        self.db = db
        self.client = NeisClient(db)
        self.meal_service = MealService(db)

    def list_regions(self) -> list[dict[str, Any]]:
        school_count_subquery = (
            select(RegionSchool.region_id, func.count(RegionSchool.id).label("school_count"))
            .where(RegionSchool.is_active.is_(True))
            .group_by(RegionSchool.region_id)
            .subquery()
        )
        rows = self.db.execute(
            select(RegionGroup, school_count_subquery.c.school_count)
            .outerjoin(school_count_subquery, school_count_subquery.c.region_id == RegionGroup.id)
            .order_by(RegionGroup.region_name.asc())
        ).all()
        return [
            {
                "id": region.id,
                "region_name": region.region_name,
                "region_type": region.region_type,
                "keyword_rules": region.keyword_rules,
                "school_count": int(school_count or 0),
            }
            for region, school_count in rows
        ]

    def create_region(self, region_name: str, region_type: str | None = None, keyword_rules: str | None = None) -> RegionGroup:
        region = RegionGroup(region_name=region_name.strip(), region_type=region_type, keyword_rules=keyword_rules)
        self.db.add(region)
        self.db.commit()
        self.db.refresh(region)
        return region

    def get_region(self, region_id: int) -> RegionGroup | None:
        return self.db.scalar(select(RegionGroup).where(RegionGroup.id == region_id))

    def get_region_schools(self, region_id: int, only_active: bool = True) -> list[RegionSchool]:
        stmt = select(RegionSchool).where(RegionSchool.region_id == region_id)
        if only_active:
            stmt = stmt.where(RegionSchool.is_active.is_(True))
        return self.db.scalars(stmt.order_by(RegionSchool.display_order.asc(), RegionSchool.school_name.asc())).all()

    async def search_school_candidates(self, query: str, region_id: int | None = None) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []

        results = await self.client.search_schools(query)
        existing_codes: set[tuple[str, str]] = set()
        if region_id is not None:
            existing_codes = {
                (item.atpt_ofcdc_sc_code, item.sd_schul_code)
                for item in self.get_region_schools(region_id, only_active=False)
            }

        rows = []
        for school in results:
            rows.append(
                {
                    "atpt_ofcdc_sc_code": school.atpt_ofcdc_sc_code,
                    "sd_schul_code": school.sd_schul_code,
                    "school_name": school.school_name,
                    "school_level": school.school_level,
                    "org_name": school.org_name,
                    "location_summary": school.location_summary,
                    "address": school.address,
                    "tel": school.tel,
                    "homepage": school.homepage,
                    "coedu": school.coedu,
                    "fond_date": school.fond_date,
                    "already_registered": (school.atpt_ofcdc_sc_code, school.sd_schul_code) in existing_codes,
                }
            )
        return rows

    async def auto_discover_candidates(self, region_id: int) -> list[dict[str, Any]]:
        region = self.get_region(region_id)
        if not region:
            raise ValueError("지역을 찾을 수 없습니다.")
        candidates = await self.search_school_candidates(region.region_name, region_id=region_id)
        for item in candidates:
            item["source"] = "auto_discover"
        return candidates

    def register_region_schools(self, region_id: int, schools: list[RegionSchoolRegisterItem | dict[str, Any]]) -> list[RegionSchool]:
        region = self.get_region(region_id)
        if not region:
            raise ValueError("지역을 찾을 수 없습니다.")

        saved: list[RegionSchool] = []
        for index, raw_item in enumerate(schools):
            item = raw_item if isinstance(raw_item, RegionSchoolRegisterItem) else RegionSchoolRegisterItem(**raw_item)
            existing = self.db.scalar(
                select(RegionSchool).where(
                    RegionSchool.region_id == region_id,
                    RegionSchool.atpt_ofcdc_sc_code == item.atpt_ofcdc_sc_code,
                    RegionSchool.sd_schul_code == item.sd_schul_code,
                )
            )
            if existing:
                existing.school_name = item.school_name
                existing.school_level = item.school_level
                existing.address = item.address
                existing.display_order = item.display_order if item.display_order is not None else index
                existing.is_active = True
                saved.append(existing)
                continue

            row = RegionSchool(
                region_id=region_id,
                atpt_ofcdc_sc_code=item.atpt_ofcdc_sc_code,
                sd_schul_code=item.sd_schul_code,
                school_name=item.school_name,
                school_level=item.school_level,
                address=item.address,
                display_order=item.display_order if item.display_order is not None else index,
                is_active=True,
            )
            self.db.add(row)
            saved.append(row)

        self.db.commit()
        for row in saved:
            self.db.refresh(row)
        return saved

    def deactivate_region_school(self, region_id: int, school_id: int) -> bool:
        row = self.db.scalar(select(RegionSchool).where(RegionSchool.id == school_id, RegionSchool.region_id == region_id))
        if not row:
            return False
        row.is_active = False
        self.db.commit()
        return True

    async def get_region_overview(self, region_id: int, target_date: date) -> dict[str, Any]:
        region = self.get_region(region_id)
        if not region:
            raise ValueError("지역을 찾을 수 없습니다.")

        schools = self.get_region_schools(region_id, only_active=True)
        tasks = [self._build_school_row(school, target_date) for school in schools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for school, result in zip(schools, results, strict=False):
            if isinstance(result, Exception):
                warnings.append(f"{school.school_name}: {result}")
                continue
            rows.append(result)

        summary = {
            "school_count": len(schools),
            "exam_count": sum(1 for item in rows if item["academic_summary"]["midterm"] or item["academic_summary"]["finalterm"]),
            "mock_exam_count": sum(1 for item in rows if item["academic_summary"]["mock_exam"]),
            "vacation_count": sum(1 for item in rows if item["academic_summary"]["summer_vacation"] or item["academic_summary"]["winter_vacation"]),
            "graduation_count": sum(1 for item in rows if item["academic_summary"]["graduation_day"]),
            "anniversary_count": sum(1 for item in rows if item["academic_summary"]["school_anniversary"]),
            "discretionary_count": sum(1 for item in rows if item["academic_summary"]["discretionary_holidays"]),
            "meal_count": sum(1 for item in rows if item.get("today_meal_summary") and item["today_meal_summary"] != "미등록"),
        }

        return {
            "region": {
                "id": region.id,
                "region_name": region.region_name,
                "region_type": region.region_type,
                "keyword_rules": region.keyword_rules,
            },
            "summary": summary,
            "rows": rows,
            "warnings": warnings,
        }

    async def get_region_meals(self, region_id: int, target_date: date) -> dict[str, Any]:
        region = self.get_region(region_id)
        if not region:
            raise ValueError("지역을 찾을 수 없습니다.")
        schools = self.get_region_schools(region_id, only_active=True)
        tasks = [self._school_meal_info(school, target_date) for school in schools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for school, result in zip(schools, results, strict=False):
            if isinstance(result, Exception):
                warnings.append(f"{school.school_name}: {result}")
                continue
            rows.append(result)

        return {
            "region": {"id": region.id, "region_name": region.region_name},
            "target_date": target_date.isoformat(),
            "rows": rows,
            "warnings": warnings,
        }

    async def get_region_schedules(self, region_id: int, start_date: date, end_date: date) -> dict[str, Any]:
        region = self.get_region(region_id)
        if not region:
            raise ValueError("지역을 찾을 수 없습니다.")
        schools = self.get_region_schools(region_id, only_active=True)
        tasks = [self._school_schedule_rows(school, start_date, end_date) for school in schools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for school, result in zip(schools, results, strict=False):
            if isinstance(result, Exception):
                warnings.append(f"{school.school_name}: {result}")
                continue
            rows.append(result)

        return {
            "region": {"id": region.id, "region_name": region.region_name},
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "rows": rows,
            "warnings": warnings,
        }

    async def _build_school_row(self, school: RegionSchool, target_date: date) -> dict[str, Any]:
        meal_info, schedule_bundle, school_info = await asyncio.gather(
            self._school_meal_info(school, target_date),
            self._school_schedule_bundle(school, target_date),
            self._school_basic_info(school),
        )

        return {
            "school_id": school.id,
            "school_name": school_info["school_name"],
            "school_level": school_info["school_level"],
            "student_count": school_info["student_count"],
            "org_name": school_info["org_name"],
            "address": school_info["address"],
            "tel": school_info["tel"],
            "homepage": school_info["homepage"],
            "today_status": schedule_bundle["today_status"],
            "academic_summary": schedule_bundle["academic_summary"],
            "ongoing_events": schedule_bundle["ongoing_events"],
            "upcoming_events": schedule_bundle["upcoming_events"],
            "today_meal_summary": meal_info["today_meal_summary"],
            "tomorrow_meal_summary": meal_info["tomorrow_meal_summary"],
        }

    async def _school_schedule_bundle(self, school: RegionSchool, target_date: date) -> dict[str, Any]:
        _school_year, year_start, year_end = school_year_range(target_date)
        rows = await self.client.get_dataset_rows(
            "SchoolSchedule",
            {
                "ATPT_OFCDC_SC_CODE": school.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": school.sd_schul_code,
                "AA_FROM_YMD": year_start.strftime("%Y%m%d"),
                "AA_TO_YMD": year_end.strftime("%Y%m%d"),
            },
        )
        events = parse_schedule_rows(rows)
        return {
            "today_status": compute_today_status(events, target_date),
            "academic_summary": build_academic_summary(events),
            "ongoing_events": ongoing_events(events, target_date),
            "upcoming_events": upcoming_events(events, target_date, within_days=14),
        }

    async def _school_schedule_rows(self, school: RegionSchool, start_date: date, end_date: date) -> dict[str, Any]:
        rows = await self.client.get_dataset_rows(
            "SchoolSchedule",
            {
                "ATPT_OFCDC_SC_CODE": school.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": school.sd_schul_code,
                "AA_FROM_YMD": start_date.strftime("%Y%m%d"),
                "AA_TO_YMD": end_date.strftime("%Y%m%d"),
            },
        )
        events = parse_schedule_rows(rows)
        filtered = [
            {
                "event_name": event["event_name"],
                "category": event["category"],
                "period": event["period"],
                "details": event["details"],
            }
            for event in events
            if not (event["end_date"] < start_date or event["start_date"] > end_date)
        ]
        return {
            "school_id": school.id,
            "school_name": school.school_name,
            "events": filtered,
        }

    async def _school_basic_info(self, school: RegionSchool) -> dict[str, Any]:
        rows = await self.client.get_dataset_rows(
            "schoolInfo",
            {
                "ATPT_OFCDC_SC_CODE": school.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": school.sd_schul_code,
            },
        )
        if not rows:
            return {
                "school_name": school.school_name,
                "school_level": school.school_level,
                "student_count": None,
                "org_name": None,
                "address": school.address,
                "tel": None,
                "homepage": None,
            }

        row = rows[0]
        return {
            "school_name": str(row.get("SCHUL_NM", "")).strip() or school.school_name,
            "school_level": str(row.get("SCHUL_KND_SC_NM", "")).strip() or school.school_level,
            "student_count": self._extract_student_count(row),
            "org_name": str(row.get("ATPT_OFCDC_SC_NM", "")).strip() or str(row.get("JU_ORG_NM", "")).strip() or None,
            "address": " ".join(filter(None, [row.get("ORG_RDNMA"), row.get("ORG_RDNDA")])).strip() or school.address,
            "tel": str(row.get("ORG_TELNO", "")).strip() or None,
            "homepage": str(row.get("HMPG_ADRES", "")).strip() or None,
        }

    async def _school_meal_info(self, school: RegionSchool, target_date: date) -> dict[str, Any]:
        rows = await self.client.get_dataset_rows(
            "mealServiceDietInfo",
            {
                "ATPT_OFCDC_SC_CODE": school.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": school.sd_schul_code,
                "MLSV_FROM_YMD": target_date.strftime("%Y%m%d"),
                "MLSV_TO_YMD": (target_date + timedelta(days=1)).strftime("%Y%m%d"),
            },
        )
        today_rows = []
        tomorrow_rows = []
        for row in rows:
            meal_date = parse_neis_date(row.get("MLSV_YMD"))
            if meal_date == target_date:
                today_rows.append(row)
            elif meal_date == target_date + timedelta(days=1):
                tomorrow_rows.append(row)

        return {
            "school_id": school.id,
            "school_name": school.school_name,
            "today_meal_summary": self._pick_meal_summary(today_rows),
            "tomorrow_meal_summary": self._pick_meal_summary(tomorrow_rows),
        }

    @staticmethod
    def _pick_meal_summary(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "미등록"
        lunch = next((item for item in rows if str(item.get("MMEAL_SC_CODE", "")).strip() == "2"), None)
        target = lunch or rows[0]
        menu_items = MealService._split_menu(str(target.get("DDISH_NM", "")))
        summary_items = [MealService.strip_allergy_codes(item.name).strip() for item in menu_items if item.name.strip()]
        if not summary_items:
            return "미등록"
        return " · ".join(summary_items[:3])

    @staticmethod
    def _extract_student_count(row: dict[str, Any]) -> int | None:
        for key in ("STUDENT_CNT", "STU_CNT", "TOT_STU_CNT", "SCHUL_TOT_STU_CNT", "SCNT"):
            value = str(row.get(key, "")).replace(",", "").strip()
            if value.isdigit():
                return int(value)
        return None
