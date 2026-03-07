from __future__ import annotations

import json
from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import StudentProfile, TimetableSnapshot
from app.schemas import TimetablePeriod
from app.services.neis_client import NeisClient
from app.utils import blocked_timetable_period, stable_hash, to_json


LEVEL_ENDPOINT_MAP = {
    "초등학교": "elsTimetable",
    "중학교": "misTimetable",
    "고등학교": "hisTimetable",
    "특수학교": "spsTimetable",
}


class TimetableService:
    def __init__(self, db: Session):
        self.db = db
        self.client = NeisClient(db)

    def endpoint_for_profile(self, profile: StudentProfile) -> str:
        return LEVEL_ENDPOINT_MAP.get(profile.school_level, "hisTimetable")

    async def get_timetable(self, profile: StudentProfile, target_date: date, force_refresh: bool = False) -> dict[str, object]:
        if blocked_timetable_period(target_date):
            return {
                "target_date": target_date,
                "periods": [],
                "message": "해당 기간의 시간표는 NEIS API 제공 제한 구간입니다.",
                "is_blocked": True,
            }
        rows = await self.client.get_dataset_rows(
            self.endpoint_for_profile(profile),
            {
                "ATPT_OFCDC_SC_CODE": profile.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": profile.sd_schul_code,
                "GRADE": profile.grade,
                "CLASS_NM": profile.class_nm,
                "ALL_TI_YMD": target_date.strftime("%Y%m%d"),
            },
            force_refresh=force_refresh,
        )
        periods = [
            TimetablePeriod(period=str(row.get("PERIO", "")).strip(), subject=str(row.get("ITRT_CNTNT", "")).strip())
            for row in rows
            if str(row.get("PERIO", "")).strip()
        ]
        periods.sort(key=lambda item: int(item.period) if item.period.isdigit() else 999)
        return {
            "target_date": target_date,
            "periods": periods,
            "message": "등록된 시간표가 없습니다." if not periods else None,
            "is_blocked": False,
        }

    async def get_week_timetable(self, profile: StudentProfile, dates: list[date], force_refresh: bool = False) -> dict[date, dict[str, object]]:
        result: dict[date, dict[str, object]] = {}
        for target_date in dates:
            result[target_date] = await self.get_timetable(profile, target_date, force_refresh=force_refresh)
        return result

    def save_snapshot(self, profile: StudentProfile, target_date: date, periods: list[TimetablePeriod]) -> tuple[bool, list[TimetablePeriod]]:
        content = [{"period": item.period, "subject": item.subject} for item in periods]
        content_hash = stable_hash(content)
        prev = self.db.scalar(
            select(TimetableSnapshot)
            .where(TimetableSnapshot.profile_id == profile.id, TimetableSnapshot.target_date == target_date)
            .order_by(desc(TimetableSnapshot.created_at))
        )
        if prev and prev.content_hash == content_hash:
            return False, []
        previous_map = {}
        if prev:
            for item in json.loads(prev.raw_json):
                previous_map[str(item["period"])] = item["subject"]
        current_map = {item["period"]: item["subject"] for item in periods}
        changed: list[TimetablePeriod] = []
        for key in sorted(set(previous_map) | set(current_map), key=lambda item: int(item) if str(item).isdigit() else 999):
            before = previous_map.get(key)
            after = current_map.get(key)
            if before != after:
                changed.append(TimetablePeriod(period=str(key), subject=after or "-", changed_from=before or "-"))
        self.db.add(TimetableSnapshot(profile_id=profile.id, target_date=target_date, raw_json=to_json(content), content_hash=content_hash))
        self.db.commit()
        return True, changed

    async def detect_changes(self, profile: StudentProfile, target_date: date) -> list[TimetablePeriod]:
        data = await self.get_timetable(profile, target_date)
        periods = data["periods"]
        if not isinstance(periods, list):
            return []
        changed, diffs = self.save_snapshot(profile, target_date, periods)
        return diffs if changed else []
