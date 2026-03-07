from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import NeisCache, NotificationLog, StudentProfile, SyncLog, User
from app.services.meal_service import MealService
from app.services.schedule_service import ScheduleService
from app.services.telegram_service import TelegramService
from app.services.timetable_service import TimetableService
from app.utils import now_kst, stable_hash, today_kst


logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: Session):
        self.db = db
        self.meal_service = MealService(db)
        self.schedule_service = ScheduleService(db)
        self.timetable_service = TimetableService(db)
        self.telegram = TelegramService()

    @property
    def base_url(self) -> str:
        from app.config import get_settings

        return get_settings().app_base_url

    def build_supply_list(self, profile: StudentProfile, periods: list) -> list[str]:
        rules = {rule.subject_name.strip(): rule.supply_text.strip() for rule in profile.supplies}
        supplies: list[str] = []
        for item in periods:
            matched = rules.get(item.subject)
            if matched and matched not in supplies:
                supplies.append(matched)
        return supplies

    async def build_daily_brief(self, profile: StudentProfile, target_date: date, label: str) -> tuple[str, list[list[dict[str, str]]]]:
        timetable = await self.timetable_service.get_timetable(profile, target_date)
        meals = await self.meal_service.get_meals(profile, target_date, target_date)
        events = await self.schedule_service.get_events(profile, target_date, target_date)
        periods = timetable["periods"]
        timetable_text = " / ".join(item.subject for item in periods[:6]) if periods else "없음"
        meal_text = "없음"
        warning = ""
        if meals:
            meal = meals[0]
            names = [item.name for item in meal.menu_items[:2]]
            meal_text = " / ".join(names) if names else "없음"
            remaining = len(meal.menu_items) - len(names)
            if remaining > 0:
                meal_text += f" 외 {remaining}종"
            if meal.allergy_warnings and profile.use_meal_allergy_alert:
                warning = f"\n• 알레르기 주의: {', '.join(meal.allergy_warnings)}"
        event_text = ", ".join(event.event_name for event in events[:2]) if events else "없음"
        supply_text = ""
        if label == "내일":
            supplies = self.build_supply_list(profile, periods)
            if supplies:
                supply_text = f"\n• 준비물: {', '.join(supplies)}"
        text = (
            f"📚 {profile.profile_name}({profile.grade}-{profile.class_nm}) {label} 브리핑\n\n"
            f"• 시간표: {timetable_text}\n"
            f"• 급식: {meal_text}\n"
            f"• 일정: {event_text}{warning}{supply_text}"
        )
        buttons = [
            [
                {"text": f"{label} 시간표", "url": f"{self.base_url}/timetable/{profile.id}?date={target_date.isoformat()}"},
                {"text": f"{label} 급식", "url": f"{self.base_url}/meal/{profile.id}?date={target_date.isoformat()}"},
            ],
            [
                {"text": "학사일정", "url": f"{self.base_url}/schedule/{profile.id}?date={target_date.isoformat()}"},
                {"text": "내 설정", "url": f"{self.base_url}/profiles/{profile.id}"},
            ],
        ]
        return text, buttons

    async def send_logged_message(
        self,
        user: User,
        profile: StudentProfile,
        notification_type: str,
        target_date: date,
        text: str,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> bool:
        if not user.telegram_chat_id or not self.telegram.enabled:
            return False
        digest = stable_hash({"text": text, "buttons": buttons})
        log = NotificationLog(
            user_id=user.id,
            profile_id=profile.id,
            notification_type=notification_type,
            target_date=target_date,
            message_hash=digest,
            status="pending",
        )
        self.db.add(log)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return False
        try:
            await self.telegram.send_message(user.telegram_chat_id, text, buttons)
            log.status = "sent"
            self.db.commit()
            return True
        except Exception as exc:
            log.status = "failed"
            log.error_message = str(exc)
            self.db.commit()
            logger.exception("Telegram send failed")
            return False

    async def send_today_briefs(self, current_time: str | None = None) -> int:
        current_time = current_time or now_kst().strftime("%H:%M")
        profiles = self.db.scalars(
            select(StudentProfile).join(User).where(
                StudentProfile.is_active.is_(True),
                StudentProfile.use_morning_alert.is_(True),
                StudentProfile.morning_alert_time == current_time,
            )
        ).all()
        sent = 0
        for profile in profiles:
            text, buttons = await self.build_daily_brief(profile, today_kst(), "오늘")
            if await self.send_logged_message(profile.user, profile, "morning_brief", today_kst(), text, buttons):
                sent += 1
        return sent

    async def send_tomorrow_briefs(self, current_time: str | None = None) -> int:
        current_time = current_time or now_kst().strftime("%H:%M")
        target_date = today_kst() + timedelta(days=1)
        profiles = self.db.scalars(
            select(StudentProfile).join(User).where(
                StudentProfile.is_active.is_(True),
                StudentProfile.use_evening_alert.is_(True),
                StudentProfile.evening_alert_time == current_time,
            )
        ).all()
        sent = 0
        for profile in profiles:
            text, buttons = await self.build_daily_brief(profile, target_date, "내일")
            school_day = await self.schedule_service.school_day_message(profile, target_date)
            text += "\n\n• 내일 학교: " + ("✅ 정상등교" if school_day == "정상등교" else school_day)
            if await self.send_logged_message(profile.user, profile, "evening_brief", target_date, text, buttons):
                sent += 1
        return sent

    async def detect_timetable_changes(self, target_dates: list[date]) -> int:
        profiles = self.db.scalars(select(StudentProfile).where(StudentProfile.is_active.is_(True), StudentProfile.use_change_alert.is_(True))).all()
        sent = 0
        for profile in profiles:
            for target_date in target_dates:
                changes = await self.timetable_service.detect_changes(profile, target_date)
                if not changes:
                    continue
                text = "\n".join([f"🔁 {profile.profile_name} 시간표 변경"] + [f"• {item.period}교시 {item.changed_from} → {item.subject}" for item in changes])
                buttons = [[{"text": "시간표 보기", "url": f"{self.base_url}/timetable/{profile.id}?date={target_date.isoformat()}"}]]
                if await self.send_logged_message(profile.user, profile, "timetable_change", target_date, text, buttons):
                    sent += 1
        return sent

    async def send_dday_alerts(self) -> int:
        today = today_kst()
        profiles = self.db.scalars(select(StudentProfile).where(StudentProfile.is_active.is_(True), StudentProfile.use_dday_alert.is_(True))).all()
        sent = 0
        for profile in profiles:
            events = await self.schedule_service.get_events(profile, today, today + timedelta(days=7))
            for event in events:
                delta = (event.date - today).days
                if delta not in (0, 1, 3, 7):
                    continue
                text = f"🗓 {profile.profile_name} {'D-Day' if delta == 0 else f'D-{delta}'}\n• {event.date.isoformat()} {event.event_name}"
                buttons = [[{"text": "학사일정", "url": f"{self.base_url}/schedule/{profile.id}?date={event.date.isoformat()}"}]]
                if await self.send_logged_message(profile.user, profile, "dday_alert", event.date, text, buttons):
                    sent += 1
        return sent

    async def prefetch_dates(self, target_dates: list[date]) -> str:
        profiles = self.db.scalars(select(StudentProfile).where(StudentProfile.is_active.is_(True))).all()
        for profile in profiles:
            for target_date in target_dates:
                await self.timetable_service.get_timetable(profile, target_date)
                await self.meal_service.get_meals(profile, target_date, target_date)
                await self.schedule_service.get_events(profile, target_date, target_date)
        return f"{len(profiles)} profiles prefetched"

    async def warm_long_term_cache(self, target_year: int | None = None) -> str:
        target_year = target_year or today_kst().year
        year_start = date(target_year, 1, 1)
        year_end = date(target_year, 12, 31)
        today = today_kst()
        upcoming_days = [today + timedelta(days=offset) for offset in range(0, 14)]
        profiles = self.db.scalars(select(StudentProfile).where(StudentProfile.is_active.is_(True))).all()

        seen_schools: set[tuple[str, str]] = set()
        for profile in profiles:
            school_key = (profile.atpt_ofcdc_sc_code, profile.sd_schul_code)
            if school_key not in seen_schools:
                seen_schools.add(school_key)
                await self.schedule_service.get_events(profile, year_start, year_end, force_refresh=True)
                await self.meal_service.client.get_school_info(profile.atpt_ofcdc_sc_code, profile.sd_schul_code, force_refresh=True)
                await self.meal_service.client.get_classes(profile.atpt_ofcdc_sc_code, profile.sd_schul_code, force_refresh=True)

            await self.meal_service.get_meals(profile, today, today + timedelta(days=14), force_refresh=True)
            await self.timetable_service.get_week_timetable(profile, upcoming_days[:7], force_refresh=True)

        return f"warmed {len(profiles)} profiles / {len(seen_schools)} schools for {target_year}"

    def cleanup_cache(self) -> int:
        deleted = self.db.execute(delete(NeisCache).where(NeisCache.expires_at < datetime.utcnow())).rowcount or 0
        self.db.commit()
        return deleted

    def log_job(self, job_name: str, status: str, message: str | None = None, started_at: datetime | None = None) -> None:
        self.db.add(SyncLog(job_name=job_name, started_at=started_at or datetime.utcnow(), finished_at=datetime.utcnow(), status=status, message=message))
        self.db.commit()
