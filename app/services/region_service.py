from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RegionGroup, RegionSchool
from app.schemas import RegionSchoolRegisterItem
from app.services.meal_service import MealService
from app.services.neis_client import NeisClient
from app.utils import parse_neis_date, school_year_range


STATUS_PRIORITY = {
    "시험중": 5,
    "방학중": 4,
    "재량휴업일": 3,
    "행사일": 2,
    "정상수업": 1,
}


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

    async def auto_discover_candidates(self, region_id: int) -> list[dict[str, Any]]:
        region = self.get_region(region_id)
        if not region:
            raise ValueError("지역을 찾을 수 없습니다.")

        keywords = self._region_keywords(region)
        results: dict[tuple[str, str], dict[str, Any]] = {}

        for keyword in keywords:
            schools = await self.client.search_schools(keyword)
            for school in schools:
                haystack = " ".join(
                    filter(
                        None,
                        [
                            school.school_name,
                            school.address,
                            school.location_summary,
                        ],
                    )
                )
                if not self._contains_keyword(haystack, keywords):
                    continue
                key = (school.atpt_ofcdc_sc_code, school.sd_schul_code)
                if key not in results:
                    results[key] = {
                        "atpt_ofcdc_sc_code": school.atpt_ofcdc_sc_code,
                        "sd_schul_code": school.sd_schul_code,
                        "school_name": school.school_name,
                        "school_level": school.school_level,
                        "address": school.address,
                        "location_summary": school.location_summary,
                        "matched_keywords": [kw for kw in keywords if self._contains_keyword(haystack, [kw])],
                    }

        existing_codes = {
            (row.atpt_ofcdc_sc_code, row.sd_schul_code)
            for row in self.db.scalars(select(RegionSchool).where(RegionSchool.region_id == region_id)).all()
        }
        candidates = []
        for value in results.values():
            value["already_registered"] = (value["atpt_ofcdc_sc_code"], value["sd_schul_code"]) in existing_codes
            candidates.append(value)
        candidates.sort(key=lambda item: (item["already_registered"], item["school_name"]))
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
            "meal_count": sum(1 for item in rows if item.get("today_meal_summary") and item.get("today_meal_summary") != "미등록"),
            "exam_count": sum(1 for item in rows if item.get("today_status") == "시험중"),
            "vacation_count": sum(1 for item in rows if item.get("today_status") == "방학중"),
            "holiday_count": sum(1 for item in rows if item.get("today_status") == "재량휴업일"),
            "event_count": sum(1 for item in rows if item.get("today_status") == "행사일"),
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
        tasks = [self._school_schedule_info(school, start_date, end_date) for school in schools]
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
        meal_info, schedule_info = await asyncio.gather(
            self._school_meal_info(school, target_date),
            self._school_schedule_overview(school, target_date),
        )
        return {
            "school_id": school.id,
            "school_name": school.school_name,
            "school_level": school.school_level,
            "student_count": None,
            "address": school.address,
            "today_status": schedule_info["today_status"],
            "today_meal_summary": meal_info["today_meal_summary"],
            "tomorrow_meal_summary": meal_info["tomorrow_meal_summary"],
            "midterm": schedule_info["midterm"],
            "finalterm": schedule_info["finalterm"],
            "summer_vacation": schedule_info["summer_vacation"],
            "winter_vacation": schedule_info["winter_vacation"],
            "graduation_day": schedule_info["graduation_day"],
            "school_anniversary": schedule_info["school_anniversary"],
            "discretionary_holidays": schedule_info["discretionary_holidays"],
            "current_events": schedule_info["current_events"],
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

    async def _school_schedule_info(self, school: RegionSchool, start_date: date, end_date: date) -> dict[str, Any]:
        rows = await self.client.get_dataset_rows(
            "SchoolSchedule",
            {
                "ATPT_OFCDC_SC_CODE": school.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": school.sd_schul_code,
                "AA_FROM_YMD": start_date.strftime("%Y%m%d"),
                "AA_TO_YMD": end_date.strftime("%Y%m%d"),
            },
        )

        items = []
        for item in self._merge_schedule_rows(rows):
            if item["end_date"] < start_date or item["start_date"] > end_date:
                continue
            items.append(
                {
                    "event_name": item["event_name"],
                    "category": item["category"],
                    "period": self._normalize_period(item["start_date"], item["end_date"], start_date),
                }
            )
        return {
            "school_id": school.id,
            "school_name": school.school_name,
            "events": items,
        }

    async def _school_schedule_overview(self, school: RegionSchool, target_date: date) -> dict[str, Any]:
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

        merged = self._merge_schedule_rows(rows)
        today_status = self._today_status(merged, target_date)

        return {
            "today_status": today_status,
            "midterm": self._first_period(merged, "midterm"),
            "finalterm": self._first_period(merged, "finalterm"),
            "summer_vacation": self._first_period(merged, "summer_vacation"),
            "winter_vacation": self._first_period(merged, "winter_vacation"),
            "graduation_day": self._first_period(merged, "graduation"),
            "school_anniversary": self._first_period(merged, "anniversary"),
            "discretionary_holidays": self._periods(merged, "discretionary_holiday"),
            "current_events": self._current_events(merged, target_date),
        }

    @staticmethod
    def _region_keywords(region: RegionGroup) -> list[str]:
        keywords = [region.region_name.strip()]
        if region.keyword_rules:
            keywords.extend([part.strip() for part in region.keyword_rules.split(",") if part.strip()])
        # stable dedupe
        seen = set()
        deduped = []
        for keyword in keywords:
            key = keyword.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(keyword)
        return deduped

    @staticmethod
    def _contains_keyword(text: str, keywords: list[str]) -> bool:
        haystack = (text or "").replace(" ", "").lower()
        return any(keyword.replace(" ", "").lower() in haystack for keyword in keywords if keyword)

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
    def _normalize_period(start_date: date, end_date: date, base_date: date) -> str:
        if start_date == end_date:
            return start_date.isoformat()
        if start_date.year == end_date.year == base_date.year:
            return f"{start_date.month}/{start_date.day}~{end_date.month}/{end_date.day}"
        return f"{start_date.isoformat()}~{end_date.isoformat()}"

    @staticmethod
    def _classify_event(event_name: str) -> str:
        name = (event_name or "").strip()
        if any(token in name for token in ("중간", "중간고사", "중간 평가")):
            return "midterm"
        if any(token in name for token in ("기말", "학기말", "기말고사")):
            return "finalterm"
        if "여름" in name and "방학" in name:
            return "summer_vacation"
        if "겨울" in name and "방학" in name:
            return "winter_vacation"
        if "졸업" in name:
            return "graduation"
        if "개교" in name and "기념" in name:
            return "anniversary"
        if "재량휴업" in name:
            return "discretionary_holiday"
        if any(token in name for token in ("공휴일", "휴업", "휴일", "대체휴일", "대체공휴일")):
            return "holiday"
        if "방학" in name:
            return "vacation"
        return "event"

    def _merge_schedule_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped_dates: dict[tuple[str, str | None], list[date]] = defaultdict(list)
        for row in rows:
            target_date = parse_neis_date(row.get("AA_YMD"))
            event_name = str(row.get("EVENT_NM", "")).strip()
            details = str(row.get("EVENT_CNTNT", "")).strip() or None
            if not target_date or not event_name:
                continue
            grouped_dates[(event_name, details)].append(target_date)

        merged: list[dict[str, Any]] = []
        for (event_name, details), dates in grouped_dates.items():
            sorted_dates = sorted(set(dates))
            start = sorted_dates[0]
            end = sorted_dates[0]
            for current in sorted_dates[1:]:
                if current == end + timedelta(days=1):
                    end = current
                    continue
                merged.append(
                    {
                        "event_name": event_name,
                        "details": details,
                        "category": self._classify_event(event_name),
                        "start_date": start,
                        "end_date": end,
                    }
                )
                start = current
                end = current
            merged.append(
                {
                    "event_name": event_name,
                    "details": details,
                    "category": self._classify_event(event_name),
                    "start_date": start,
                    "end_date": end,
                }
            )

        merged.sort(key=lambda item: (item["start_date"], item["event_name"]))
        return merged

    def _today_status(self, merged_events: list[dict[str, Any]], target_date: date) -> str:
        active_categories = [
            event["category"]
            for event in merged_events
            if event["start_date"] <= target_date <= event["end_date"]
        ]
        if not active_categories:
            return "정상수업"

        statuses = []
        for category in active_categories:
            if category in {"midterm", "finalterm"}:
                statuses.append("시험중")
            elif category in {"summer_vacation", "winter_vacation", "vacation"}:
                statuses.append("방학중")
            elif category == "discretionary_holiday":
                statuses.append("재량휴업일")
            elif category in {"event", "anniversary", "graduation", "holiday"}:
                statuses.append("행사일")

        if not statuses:
            return "정상수업"
        return max(statuses, key=lambda value: STATUS_PRIORITY[value])

    def _first_period(self, merged_events: list[dict[str, Any]], category: str) -> str | None:
        for event in merged_events:
            if event["category"] == category:
                return self._normalize_period(event["start_date"], event["end_date"], event["start_date"])
        return None

    def _periods(self, merged_events: list[dict[str, Any]], category: str) -> list[str]:
        return [
            self._normalize_period(event["start_date"], event["end_date"], event["start_date"])
            for event in merged_events
            if event["category"] == category
        ]

    def _current_events(self, merged_events: list[dict[str, Any]], target_date: date) -> list[str]:
        rows = []
        for event in merged_events:
            if event["start_date"] <= target_date <= event["end_date"]:
                rows.append(event["event_name"])
        return rows
