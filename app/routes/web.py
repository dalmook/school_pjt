from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import NotificationLog, StudentProfile, SyncLog, User
from app.schemas import CalendarDay, EmptyState, ErrorState, ScheduleEntry
from app.services.auth_service import SESSION_COOKIE, authenticate_user, create_user, decode_session, encode_session
from app.services.meal_service import MealService
from app.services.notification_service import NotificationService
from app.services.profile_service import replace_profile_rules
from app.services.schedule_service import ScheduleService
from app.services.timetable_service import TimetableService
from app.utils import ALLERGY_CODE_MAP, daterange, school_year_range, today_kst


router = APIRouter()
logger = logging.getLogger(__name__)


def templates(request: Request):
    return request.app.state.templates


def get_current_user(request: Request, db: Session) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = decode_session(token)
    if not user_id:
        request.state.user = None
        return None
    user = db.scalar(select(User).where(User.id == user_id))
    request.state.user = user
    return user


def require_login(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user or not user.username:
        raise PermissionError("login_required")
    return user


def render(request: Request, template_name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates(request).TemplateResponse(template_name, {"request": request, "current_user": getattr(request.state, "user", None), **context}, status_code=status_code)


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def month_param_to_date(month: str | None, fallback: date) -> date:
    if not month:
        return fallback.replace(day=1)
    try:
        return datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError:
        return fallback.replace(day=1)


def add_months(base: date, delta: int) -> date:
    month_index = (base.year * 12 + (base.month - 1)) + delta
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def build_schedule_calendar(events: list[ScheduleEntry], month_start: date, detail_date: date, request: Request, view: str) -> list[list[CalendarDay]]:
    event_map: dict[date, list[ScheduleEntry]] = defaultdict(list)
    for event in events:
        event_map[event.date].append(event)
    month_cal = calendar.Calendar(firstweekday=6).monthdatescalendar(month_start.year, month_start.month)
    weeks: list[list[CalendarDay]] = []
    today = today_kst()
    for week in month_cal:
        cells: list[CalendarDay] = []
        for day in week:
            href = str(request.url.include_query_params(month=month_start.strftime("%Y-%m"), view=view, detail=day.isoformat()))
            day_events = event_map.get(day, [])
            cells.append(
                CalendarDay(
                    date=day,
                    is_current_month=day.month == month_start.month,
                    is_today=day == today,
                    is_selected=day == detail_date,
                    events=day_events[:2],
                    hidden_count=max(len(day_events) - 2, 0),
                    href=href,
                )
            )
        weeks.append(cells)
    return weeks


def first_event_date(events: list[ScheduleEntry], month_start: date) -> date:
    for event in events:
        if event.date.month == month_start.month:
            return event.date
    return month_start


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    profile = user.profiles[0] if user and user.profiles else None
    today = today_kst()
    tomorrow = today + timedelta(days=1)
    context = {
        "profile": profile,
        "today": today,
        "tomorrow": tomorrow,
        "settings": get_settings(),
        "today_brief": "브리핑을 불러오지 못했습니다.",
        "tomorrow_brief": "브리핑을 불러오지 못했습니다.",
        "week_events": [],
        "change_preview": [],
        "today_meals": [],
        "empty_state": None,
        "error_state": None,
    }
    if not user:
        context["empty_state"] = EmptyState(
            title="로그인 후 자녀 프로필을 관리할 수 있습니다.",
            description="간단한 아이디와 비밀번호로 가입한 뒤 학교와 학급을 등록하세요.",
            action_label="로그인",
            action_href="/login",
        )
        return render(request, "home.html", context)
    if not profile:
        context["empty_state"] = EmptyState(
            title="등록된 학교 정보가 없습니다.",
            description="프로필에서 학교와 학급을 먼저 등록하면 오늘 브리핑을 볼 수 있습니다.",
            action_label="학교 등록",
            action_href="/profiles/new",
        )
        return render(request, "home.html", context)

    timetable_service = TimetableService(db)
    schedule_service = ScheduleService(db)
    notifier = NotificationService(db)
    try:
        context["today_brief"], _ = await notifier.build_daily_brief(profile, today, "오늘")
        context["tomorrow_brief"], _ = await notifier.build_daily_brief(profile, tomorrow, "내일")
        context["week_events"] = await schedule_service.get_events(profile, today, today + timedelta(days=6))
        context["change_preview"] = await timetable_service.detect_changes(profile, today)
        context["today_meals"] = await MealService(db).get_meals(profile, today, today)
    except Exception as exc:
        logger.exception("Failed to load home data profile_id=%s", profile.id)
        context["error_state"] = ErrorState(title="홈 브리핑을 불러오지 못했습니다.", description=str(exc))
    return render(request, "home.html", context)


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, db: Session = Depends(get_db)):
    get_current_user(request, db)
    return render(request, "auth_signup.html", {"settings": get_settings(), "error_message": None})


@router.post("/signup")
async def signup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    get_current_user(request, db)
    username = username.strip()
    error_message = None
    if len(username) < 3:
        error_message = "아이디는 3자 이상이어야 합니다."
    elif len(password) < 4:
        error_message = "비밀번호는 4자 이상이어야 합니다."
    elif password != password_confirm:
        error_message = "비밀번호 확인이 일치하지 않습니다."
    if error_message:
        return render(request, "auth_signup.html", {"settings": get_settings(), "error_message": error_message}, status_code=400)
    try:
        user = create_user(db, username, password)
    except IntegrityError:
        db.rollback()
        return render(request, "auth_signup.html", {"settings": get_settings(), "error_message": "이미 사용 중인 아이디입니다."}, status_code=400)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, encode_session(user.id), httponly=True, samesite="lax")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    get_current_user(request, db)
    return render(request, "auth_login.html", {"settings": get_settings(), "error_message": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    get_current_user(request, db)
    user = authenticate_user(db, username, password)
    if not user:
        return render(request, "auth_login.html", {"settings": get_settings(), "error_message": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=400)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, encode_session(user.id), httponly=True, samesite="lax")
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/profiles", response_class=HTMLResponse)
async def profile_list(request: Request, db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    return render(request, "profiles.html", {"profiles": user.profiles, "settings": get_settings()})


@router.get("/profiles/new", response_class=HTMLResponse)
async def profile_new(request: Request, db: Session = Depends(get_db)):
    try:
        require_login(request, db)
    except PermissionError:
        return login_redirect()
    return render(request, "profile_form.html", {"profile": None, "allergy_map": ALLERGY_CODE_MAP, "settings": get_settings()})


@router.post("/profiles")
async def profile_create(
    request: Request,
    profile_name: str = Form(...),
    atpt_ofcdc_sc_code: str = Form(...),
    sd_schul_code: str = Form(...),
    school_name: str = Form(...),
    school_level: str = Form(...),
    grade: int = Form(...),
    class_nm: str = Form(...),
    morning_alert_time: str = Form("07:00"),
    evening_alert_time: str = Form("21:00"),
    school_address: str = Form(""),
    school_tel: str = Form(""),
    school_homepage: str = Form(""),
    allergy_codes: list[str] = Form(default=[]),
    allergy_names: list[str] = Form(default=[]),
    supply_subjects: list[str] = Form(default=[]),
    supply_texts: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = StudentProfile(
        user_id=user.id,
        profile_name=profile_name,
        atpt_ofcdc_sc_code=atpt_ofcdc_sc_code,
        sd_schul_code=sd_schul_code,
        school_name=school_name,
        school_level=school_level,
        grade=grade,
        class_nm=class_nm,
        school_address=school_address or None,
        school_tel=school_tel or None,
        school_homepage=school_homepage or None,
        morning_alert_time=morning_alert_time,
        evening_alert_time=evening_alert_time,
    )
    db.add(profile)
    db.flush()
    replace_profile_rules(profile, allergy_codes, allergy_names, list(zip(supply_subjects, supply_texts)))
    db.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.get("/profiles/{profile_id}", response_class=HTMLResponse)
async def profile_detail(profile_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if not profile:
        return RedirectResponse("/profiles", status_code=303)
    return render(request, "profile_detail.html", {"profile": profile, "settings": get_settings()})


@router.get("/profiles/{profile_id}/edit", response_class=HTMLResponse)
async def profile_edit(profile_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if not profile:
        return RedirectResponse("/profiles", status_code=303)
    return render(request, "profile_form.html", {"profile": profile, "allergy_map": ALLERGY_CODE_MAP, "settings": get_settings()})


@router.post("/profiles/{profile_id}")
async def profile_update(
    profile_id: int,
    request: Request,
    profile_name: str = Form(...),
    grade: int = Form(...),
    class_nm: str = Form(...),
    morning_alert_time: str = Form("07:00"),
    evening_alert_time: str = Form("21:00"),
    allergy_codes: list[str] = Form(default=[]),
    allergy_names: list[str] = Form(default=[]),
    supply_subjects: list[str] = Form(default=[]),
    supply_texts: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if not profile:
        return RedirectResponse("/profiles", status_code=303)
    profile.profile_name = profile_name
    profile.grade = grade
    profile.class_nm = class_nm
    profile.morning_alert_time = morning_alert_time
    profile.evening_alert_time = evening_alert_time
    replace_profile_rules(profile, allergy_codes, allergy_names, list(zip(supply_subjects, supply_texts)))
    db.commit()
    return RedirectResponse(f"/profiles/{profile_id}", status_code=303)


@router.post("/profiles/{profile_id}/delete")
async def profile_delete(profile_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if profile:
        db.delete(profile)
        db.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.get("/timetable/{profile_id}", response_class=HTMLResponse)
async def timetable_page(profile_id: int, request: Request, date: str | None = None, tab: str = "week", db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if not profile:
        return RedirectResponse("/profiles", status_code=303)
    try:
        base_date = datetime.fromisoformat(date).date() if date else today_kst()
    except ValueError:
        base_date = today_kst()
    start_of_week = base_date - timedelta(days=base_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    target_dates = daterange(start_of_week, end_of_week)
    error_state = None
    empty_state = None
    try:
        week = await TimetableService(db).get_week_timetable(profile, target_dates)
    except Exception as exc:
        logger.exception("Failed to load timetable profile_id=%s", profile_id)
        week = {base_date: {"target_date": base_date, "periods": [], "message": "시간표를 불러오지 못했습니다.", "is_blocked": False}}
        error_state = ErrorState(title="시간표를 불러오지 못했습니다.", description=str(exc))
    if not error_state and all(not data.get("periods") for data in week.values()):
        empty_state = EmptyState(
            title="조회된 시간표가 없습니다.",
            description="주말, 방학, 휴업일이거나 아직 NEIS에 시간표가 등록되지 않았을 수 있습니다.",
            action_label="오늘 보기",
            action_href=f"/timetable/{profile.id}",
        )
    return render(request, "timetable.html", {"profile": profile, "week": week, "selected_date": base_date, "tab": tab, "week_start": start_of_week, "week_end": end_of_week, "prev_date": base_date - timedelta(days=1), "next_date": base_date + timedelta(days=1), "error_state": error_state, "empty_state": empty_state, "settings": get_settings()})


@router.get("/meal/{profile_id}", response_class=HTMLResponse)
async def meal_page(profile_id: int, request: Request, date: str | None = None, tab: str = "week", db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if not profile:
        return RedirectResponse("/profiles", status_code=303)
    base_date = datetime.fromisoformat(date).date() if date else today_kst()
    start_of_week = base_date - timedelta(days=base_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    error_state = None
    empty_state = None
    try:
        meals = await MealService(db).get_meals(profile, start_of_week, end_of_week)
    except Exception as exc:
        logger.exception("Failed to load meals profile_id=%s", profile_id)
        meals = []
        error_state = ErrorState(title="급식 정보를 불러오지 못했습니다.", description=str(exc))
    selected_meals = [meal for meal in meals if meal.date == base_date]
    if not meals and not error_state:
        empty_state = EmptyState(title="해당 날짜에는 급식 정보가 없습니다.", description="주말, 공휴일, 방학 기간에는 급식이 없을 수 있습니다.", action_label="오늘로 이동", action_href=f"/meal/{profile.id}")
    return render(request, "meal.html", {"profile": profile, "meals": meals, "selected_meals": selected_meals, "selected_date": base_date, "tab": tab, "week_start": start_of_week, "week_end": end_of_week, "prev_date": base_date - timedelta(days=1), "next_date": base_date + timedelta(days=1), "error_state": error_state, "empty_state": empty_state, "settings": get_settings()})


@router.get("/schedule/{profile_id}", response_class=HTMLResponse)
async def schedule_page(profile_id: int, request: Request, month: str | None = None, view: str = "month", detail: str | None = None, db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id, StudentProfile.user_id == user.id))
    if not profile:
        return RedirectResponse("/profiles", status_code=303)
    month_start = month_param_to_date(month, today_kst())
    prev_month = add_months(month_start, -1)
    next_month = add_months(month_start, 1)
    school_year, year_start, year_end = school_year_range(month_start)
    error_state = None
    empty_state = None
    try:
        events = await ScheduleService(db).get_events(profile, year_start, year_end)
    except Exception as exc:
        logger.exception("Failed to load schedule profile_id=%s", profile_id)
        events = []
        error_state = ErrorState(title="학사일정을 불러오지 못했습니다.", description=str(exc))
    year_events = [event for event in events if year_start <= event.date <= year_end]
    month_events = [event for event in year_events if event.date.year == month_start.year and event.date.month == month_start.month]
    if detail:
        try:
            detail_date = datetime.fromisoformat(detail).date()
        except ValueError:
            detail_date = first_event_date(month_events, month_start)
    else:
        detail_date = first_event_date(month_events, month_start)
    detail_entries = [event for event in month_events if event.date == detail_date]
    upcoming_events = [event for event in year_events if event.dday is not None and event.dday >= 0][:8]
    calendar_weeks = build_schedule_calendar(month_events, month_start, detail_date, request, view)
    if not month_events and not error_state:
        empty_state = EmptyState(title="방학 기간이거나 조회된 일정이 없습니다.", description="선택한 월에 등록된 학사일정이 없으면 빈 달력으로 표시됩니다.", action_label="이번 달 보기", action_href=f"/schedule/{profile.id}")
    return render(request, "schedule.html", {"profile": profile, "events": month_events, "calendar_weeks": calendar_weeks, "selected_date": detail_date, "detail_entries": detail_entries, "upcoming_events": upcoming_events, "month_start": month_start, "view": view, "prev_month": prev_month.strftime("%Y-%m"), "next_month": next_month.strftime("%Y-%m"), "error_state": error_state, "empty_state": empty_state, "settings": get_settings()})


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, token: str, db: Session = Depends(get_db)):
    get_current_user(request, db)
    if token != get_settings().admin_token:
        return HTMLResponse("forbidden", status_code=403)
    sync_logs = db.scalars(select(SyncLog).order_by(SyncLog.started_at.desc()).limit(50)).all()
    notification_logs = db.scalars(select(NotificationLog).order_by(NotificationLog.sent_at.desc()).limit(50)).all()
    return render(request, "admin_logs.html", {"sync_logs": sync_logs, "notification_logs": notification_logs, "settings": get_settings()})


@router.get("/telegram/connect")
async def telegram_connect(request: Request, db: Session = Depends(get_db)):
    try:
        user = require_login(request, db)
    except PermissionError:
        return login_redirect()
    username = get_settings().telegram_bot_username
    if not username:
        return RedirectResponse("/profiles", status_code=303)
    deep_link = f"https://t.me/{username}?start=connect_{user.id}"
    start_code = f"connect_{user.id}"
    return render(request, "telegram_connect.html", {"settings": get_settings(), "telegram_username": username, "deep_link": deep_link, "start_code": start_code, "user": user})
