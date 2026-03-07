from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import StudentProfile, User
from app.services.meal_service import MealService
from app.services.neis_client import NeisClient
from app.services.notification_service import NotificationService
from app.services.profile_service import get_or_create_telegram_user, get_telegram_session
from app.services.schedule_service import ScheduleService
from app.services.telegram_service import TelegramService
from app.utils import today_kst


router = APIRouter(prefix="/telegram")


def parse_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return "", stripped
    parts = stripped.split(maxsplit=1)
    command_token = parts[0]
    payload = parts[1] if len(parts) > 1 else ""
    command = command_token.split("@", 1)[0].lower()
    return command, payload.strip()


def menu_buttons(profile_id: int) -> list[list[dict[str, str]]]:
    base = get_settings().app_base_url
    return [
        [
            {"text": "오늘 시간표", "url": f"{base}/timetable/{profile_id}"},
            {"text": "오늘 급식", "url": f"{base}/meal/{profile_id}"},
        ],
        [
            {"text": "학사일정", "url": f"{base}/schedule/{profile_id}"},
            {"text": "내 설정", "url": f"{base}/profiles/{profile_id}"},
        ],
    ]


async def send_profile_selector(service: TelegramService, user: User, title: str, prefix: str) -> None:
    keyboard = [[{"text": f"{profile.profile_name} ({profile.grade}-{profile.class_nm})", "callback_data": f"{prefix}:{profile.id}"}] for profile in user.profiles]
    await service.send_message(user.telegram_chat_id or "", title, keyboard)


async def send_profile_brief(db: Session, user: User, profile: StudentProfile, mode: str) -> None:
    notifier = NotificationService(db)
    target_date = today_kst() if mode == "today" else today_kst() + timedelta(days=1)
    label = "오늘" if mode == "today" else "내일"
    text, _ = await notifier.build_daily_brief(profile, target_date, label)
    await notifier.telegram.send_message(user.telegram_chat_id or "", text, menu_buttons(profile.id))


async def send_profile_meal(db: Session, user: User, profile: StudentProfile) -> None:
    meals = await MealService(db).get_meals(profile, today_kst(), today_kst() + timedelta(days=1))
    if not meals:
        text = f"🍽 {profile.profile_name}\n오늘/내일 급식 정보가 없습니다."
    else:
        lines = [f"🍽 {profile.profile_name} 급식"]
        for meal in meals[:2]:
            sample = ", ".join(item.name for item in meal.menu_items[:4])
            lines.append(f"• {meal.date.isoformat()} {sample}")
            if meal.allergy_warnings:
                lines.append(f"⚠ {', '.join(meal.allergy_warnings)}")
        text = "\n".join(lines)
    await TelegramService().send_message(user.telegram_chat_id or "", text, menu_buttons(profile.id))


async def send_profile_schedule(db: Session, user: User, profile: StudentProfile) -> None:
    events = await ScheduleService(db).get_events(profile, today_kst(), today_kst() + timedelta(days=7))
    if not events:
        text = f"🗓 {profile.profile_name}\n이번 주 학사일정이 없습니다."
    else:
        lines = [f"🗓 {profile.profile_name} 이번 주 일정"]
        for event in events[:5]:
            lines.append(f"• {event.date.isoformat()} [{event.badge}] {event.event_name}")
        text = "\n".join(lines)
    await TelegramService().send_message(user.telegram_chat_id or "", text, menu_buttons(profile.id))


async def handle_command(db: Session, user: User, text: str) -> None:
    telegram = TelegramService()
    session = get_telegram_session(db, user.id)
    command, payload_text = parse_command(text)

    if command == "/start":
        if payload_text.startswith("connect_"):
            try:
                link_user_id = int(payload_text.split("_", 1)[1])
            except ValueError:
                link_user_id = 0
            if link_user_id:
                target = db.scalar(select(User).where(User.id == link_user_id))
                if target:
                    target.telegram_user_id = user.telegram_user_id
                    target.telegram_chat_id = user.telegram_chat_id
                    db.commit()
                    await telegram.send_message(user.telegram_chat_id or "", "웹 계정과 연결되었습니다.")
                    return
        await telegram.send_message(user.telegram_chat_id or "", "학교 생활 알림 봇입니다.\n/register 등록\n/profiles 목록\n/today 오늘\n/tomorrow 내일")
        return

    if command == "/help":
        await telegram.send_message(user.telegram_chat_id or "", "/register /profiles /today /tomorrow /meal /schedule /settings")
        return

    if command == "/register":
        session.state = "await_school_query"
        session.payload_json = "{}"
        db.commit()
        await telegram.send_message(user.telegram_chat_id or "", "학교명을 입력해 주세요.")
        return

    if command == "/profiles":
        if not user.profiles:
            await telegram.send_message(user.telegram_chat_id or "", "등록된 프로필이 없습니다. /register 로 등록해 주세요.")
            return
        keyboard = []
        for profile in user.profiles:
            keyboard.append(
                [
                    {"text": f"{profile.profile_name} 오늘", "callback_data": f"profile_today:{profile.id}"},
                    {"text": "내일", "callback_data": f"profile_tomorrow:{profile.id}"},
                    {"text": "삭제", "callback_data": f"profile_delete:{profile.id}"},
                ]
            )
        await telegram.send_message(user.telegram_chat_id or "", "등록된 프로필입니다.", keyboard)
        return

    if command == "/today":
        if len(user.profiles) == 1:
            await send_profile_brief(db, user, user.profiles[0], "today")
        elif user.profiles:
            await send_profile_selector(telegram, user, "오늘 브리핑을 볼 프로필을 선택해 주세요.", "brief_today")
        else:
            await telegram.send_message(user.telegram_chat_id or "", "등록된 프로필이 없습니다.")
        return

    if command == "/tomorrow":
        if len(user.profiles) == 1:
            await send_profile_brief(db, user, user.profiles[0], "tomorrow")
        elif user.profiles:
            await send_profile_selector(telegram, user, "내일 브리핑을 볼 프로필을 선택해 주세요.", "brief_tomorrow")
        else:
            await telegram.send_message(user.telegram_chat_id or "", "등록된 프로필이 없습니다.")
        return

    if command == "/meal":
        if len(user.profiles) == 1:
            await send_profile_meal(db, user, user.profiles[0])
        elif user.profiles:
            await send_profile_selector(telegram, user, "급식 정보를 볼 프로필을 선택해 주세요.", "meal")
        else:
            await telegram.send_message(user.telegram_chat_id or "", "등록된 프로필이 없습니다.")
        return

    if command == "/schedule":
        if len(user.profiles) == 1:
            await send_profile_schedule(db, user, user.profiles[0])
        elif user.profiles:
            await send_profile_selector(telegram, user, "학사일정을 볼 프로필을 선택해 주세요.", "schedule")
        else:
            await telegram.send_message(user.telegram_chat_id or "", "등록된 프로필이 없습니다.")
        return

    if command == "/settings":
        await telegram.send_message(user.telegram_chat_id or "", "설정은 웹에서 관리할 수 있습니다.", [[{"text": "설정 열기", "url": f"{get_settings().app_base_url}/profiles"}]])
        return

    payload = json.loads(session.payload_json or "{}")
    if session.state == "await_school_query":
        schools = await NeisClient(db).search_schools(text.strip())
        payload["schools"] = [item.model_dump() for item in schools[:8]]
        session.state = "await_school_select"
        session.payload_json = json.dumps(payload, ensure_ascii=False)
        db.commit()
        keyboard = [[{"text": f"{item.school_name} ({item.school_level})", "callback_data": f"reg_school:{idx}"}] for idx, item in enumerate(schools[:8])]
        await telegram.send_message(user.telegram_chat_id or "", "학교를 선택해 주세요.", keyboard)
        return

    if session.state == "await_profile_name":
        school = payload["selected_school"]
        profile = StudentProfile(
            user_id=user.id,
            profile_name=text.strip(),
            atpt_ofcdc_sc_code=school["atpt_ofcdc_sc_code"],
            sd_schul_code=school["sd_schul_code"],
            school_name=school["school_name"],
            school_level=school["school_level"],
            grade=int(payload["selected_grade"]),
            class_nm=str(payload["selected_class"]),
            school_address=school.get("address"),
            school_tel=school.get("tel"),
            school_homepage=school.get("homepage"),
        )
        db.add(profile)
        session.state = "idle"
        session.payload_json = "{}"
        db.commit()
        await telegram.send_message(user.telegram_chat_id or "", f"{profile.profile_name} 프로필이 등록되었습니다.", menu_buttons(profile.id))


async def handle_callback(db: Session, callback_query: dict) -> None:
    telegram = TelegramService()
    data = callback_query["data"]
    message = callback_query["message"]
    user = get_or_create_telegram_user(db, str(callback_query["from"]["id"]), str(message["chat"]["id"]))
    session = get_telegram_session(db, user.id)
    payload = json.loads(session.payload_json or "{}")

    if data.startswith("reg_school:"):
        idx = int(data.split(":")[1])
        school = payload["schools"][idx]
        payload["selected_school"] = school
        classes = await NeisClient(db).get_classes(school["atpt_ofcdc_sc_code"], school["sd_schul_code"])
        payload["classes"] = [item.model_dump() for item in classes]
        session.state = "await_grade_select"
        session.payload_json = json.dumps(payload, ensure_ascii=False)
        db.commit()
        grades = sorted({item.grade for item in classes})
        keyboard = [[{"text": f"{grade}학년", "callback_data": f"reg_grade:{grade}"}] for grade in grades]
        await telegram.send_message(user.telegram_chat_id or "", "학년을 선택해 주세요.", keyboard)
    elif data.startswith("reg_grade:"):
        grade = int(data.split(":")[1])
        payload["selected_grade"] = grade
        classes = [item for item in payload["classes"] if item["grade"] == grade]
        session.state = "await_class_select"
        session.payload_json = json.dumps(payload, ensure_ascii=False)
        db.commit()
        keyboard = [[{"text": f"{item['class_nm']}반", "callback_data": f"reg_class:{item['class_nm']}"}] for item in classes]
        await telegram.send_message(user.telegram_chat_id or "", "반을 선택해 주세요.", keyboard)
    elif data.startswith("reg_class:"):
        payload["selected_class"] = data.split(":")[1]
        session.state = "await_profile_name"
        session.payload_json = json.dumps(payload, ensure_ascii=False)
        db.commit()
        await telegram.send_message(user.telegram_chat_id or "", "프로필명을 입력해 주세요. 예: 민준")
    elif data.startswith("brief_today:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            await send_profile_brief(db, user, profile, "today")
    elif data.startswith("brief_tomorrow:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            await send_profile_brief(db, user, profile, "tomorrow")
    elif data.startswith("meal:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            await send_profile_meal(db, user, profile)
    elif data.startswith("schedule:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            await send_profile_schedule(db, user, profile)
    elif data.startswith("profile_today:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            await send_profile_brief(db, user, profile, "today")
    elif data.startswith("profile_tomorrow:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            await send_profile_brief(db, user, profile, "tomorrow")
    elif data.startswith("profile_delete:"):
        profile = db.scalar(select(StudentProfile).where(StudentProfile.id == int(data.split(":")[1]), StudentProfile.user_id == user.id))
        if profile:
            db.delete(profile)
            db.commit()
            await telegram.send_message(user.telegram_chat_id or "", f"{profile.profile_name} 프로필을 삭제했습니다.")
    await telegram.answer_callback(callback_query["id"], "처리되었습니다.")


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if get_settings().telegram_bot_token and x_telegram_bot_api_secret_token != get_settings().webhook_secret:
        raise HTTPException(status_code=403, detail="invalid telegram secret")
    payload = await request.json()
    if "message" in payload and payload["message"].get("text"):
        message = payload["message"]
        user = get_or_create_telegram_user(db, str(message["from"]["id"]), str(message["chat"]["id"]))
        await handle_command(db, user, message["text"])
    elif "callback_query" in payload:
        await handle_callback(db, payload["callback_query"])
    return {"ok": True}
