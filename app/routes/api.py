from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import StudentProfile
from app.services.meal_service import MealService
from app.services.neis_client import NeisClient
from app.services.notification_service import NotificationService
from app.services.schedule_service import ScheduleService
from app.services.timetable_service import TimetableService
from app.utils import today_kst


router = APIRouter(prefix="/api")


@router.get("/schools/search")
async def search_schools(q: str, db: Session = Depends(get_db)):
    if not q.strip():
        return []
    return [item.model_dump() for item in await NeisClient(db).search_schools(q.strip())]


@router.get("/schools/{school_code}/classes")
async def school_classes(school_code: str, atpt_code: str, db: Session = Depends(get_db)):
    try:
        return [item.model_dump() for item in await NeisClient(db).get_classes(atpt_code, school_code)]
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/profile/{profile_id}/today")
async def profile_today(profile_id: int, db: Session = Depends(get_db)):
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    text, _ = await NotificationService(db).build_daily_brief(profile, today_kst(), "오늘")
    return {"message": text}


@router.get("/profile/{profile_id}/tomorrow")
async def profile_tomorrow(profile_id: int, db: Session = Depends(get_db)):
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    text, _ = await NotificationService(db).build_daily_brief(profile, today_kst() + timedelta(days=1), "내일")
    return {"message": text}


@router.get("/profile/{profile_id}/timetable")
async def profile_timetable(profile_id: int, db: Session = Depends(get_db)):
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    data = await TimetableService(db).get_timetable(profile, today_kst())
    return {"target_date": str(data["target_date"]), "message": data["message"], "periods": [item.model_dump() for item in data["periods"]]}


@router.get("/profile/{profile_id}/meal")
async def profile_meal(profile_id: int, db: Session = Depends(get_db)):
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    meals = await MealService(db).get_meals(profile, today_kst(), today_kst() + timedelta(days=6))
    return {"meals": [item.model_dump() for item in meals]}


@router.get("/profile/{profile_id}/schedule")
async def profile_schedule(profile_id: int, db: Session = Depends(get_db)):
    profile = db.scalar(select(StudentProfile).where(StudentProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    events = await ScheduleService(db).get_events(profile, today_kst(), today_kst() + timedelta(days=30))
    return {"events": [item.model_dump() for item in events]}
