from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import StudentProfile
from app.schemas import ScheduleEntry
from app.services.neis_client import NeisClient
from app.utils import parse_neis_date, today_kst


DAY_OFF_KEYWORDS = ("휴업", "방학", "재량휴업", "개교기념일", "대체공휴일", "공휴일")
VACATION_KEYWORDS = ("방학",)
EXAM_KEYWORDS = ("고사", "시험", "평가")
EVENT_KEYWORDS = ("체험", "행사", "축제", "공연", "발표")


class ScheduleService:
    def __init__(self, db: Session):
        self.db = db
        self.client = NeisClient(db)

    @staticmethod
    def _badge_for_event(name: str) -> tuple[str, str, bool]:
        if any(keyword in name for keyword in VACATION_KEYWORDS):
            return "방학", "vacation", True
        if any(keyword in name for keyword in DAY_OFF_KEYWORDS):
            return "휴업", "dayoff", True
        if any(keyword in name for keyword in EXAM_KEYWORDS):
            return "시험", "exam", False
        if any(keyword in name for keyword in EVENT_KEYWORDS):
            return "행사", "event", False
        return "기타", "neutral", False

    async def get_events(self, profile: StudentProfile, start_date: date, end_date: date, force_refresh: bool = False) -> list[ScheduleEntry]:
        rows = await self.client.get_dataset_rows(
            "SchoolSchedule",
            {
                "ATPT_OFCDC_SC_CODE": profile.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": profile.sd_schul_code,
                "AA_FROM_YMD": start_date.strftime("%Y%m%d"),
                "AA_TO_YMD": end_date.strftime("%Y%m%d"),
            },
            force_refresh=force_refresh,
        )
        events: list[ScheduleEntry] = []
        today = today_kst()
        for row in rows:
            target = parse_neis_date(row.get("AA_YMD"))
            name = str(row.get("EVENT_NM", "")).strip()
            if not target or not name:
                continue
            badge, badge_tone, is_day_off = self._badge_for_event(name)
            events.append(
                ScheduleEntry(
                    date=target,
                    event_name=name,
                    details=str(row.get("EVENT_CNTNT", "")).strip() or None,
                    badge=badge,
                    badge_tone=badge_tone,
                    is_day_off=is_day_off,
                    dday=(target - today).days,
                )
            )
        events.sort(key=lambda item: (item.date, item.event_name))
        return events

    async def school_day_message(self, profile: StudentProfile, target_date: date) -> str:
        if target_date.weekday() >= 5:
            return "주말입니다."
        events = await self.get_events(profile, target_date, target_date)
        if not events:
            return "정상등교"
        off_event = next((event for event in events if event.is_day_off), None)
        if off_event:
            return off_event.event_name
        exam_event = next((event for event in events if event.badge == "시험"), None)
        if exam_event:
            return exam_event.event_name
        return events[0].event_name
