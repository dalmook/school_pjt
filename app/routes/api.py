from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import StudentProfile
from app.schemas import RegionCreateRequest, RegionDetailOut, RegionOut, RegionSchoolOut, RegionSchoolRegisterRequest
from app.services.meal_service import MealService
from app.services.neis_client import NeisClient
from app.services.notification_service import NotificationService
from app.services.region_service import RegionService
from app.services.schedule_service import ScheduleService
from app.services.timetable_service import TimetableService
from app.utils import today_kst


router = APIRouter(prefix="/api")


@router.get("/schools/search")
async def search_schools(q: str, region_id: int | None = None, db: Session = Depends(get_db)):
    if not q.strip():
        return []
    try:
        return await RegionService(db).search_school_candidates(q.strip(), region_id=region_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


@router.get("/regions")
async def list_regions(db: Session = Depends(get_db)):
    return {"regions": RegionService(db).list_regions()}


@router.post("/regions")
async def create_region(payload: RegionCreateRequest, db: Session = Depends(get_db)):
    try:
        region = RegionService(db).create_region(payload.region_name, payload.region_type, payload.keyword_rules)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 존재하는 지역명입니다.") from exc
    return {"region": RegionOut(id=region.id, region_name=region.region_name, region_type=region.region_type, keyword_rules=region.keyword_rules).model_dump()}


@router.get("/regions/{region_id}")
async def region_detail(region_id: int, db: Session = Depends(get_db)):
    service = RegionService(db)
    region = service.get_region(region_id)
    if not region:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다.")
    schools = service.get_region_schools(region_id, only_active=False)
    data = RegionDetailOut(
        region=RegionOut(id=region.id, region_name=region.region_name, region_type=region.region_type, keyword_rules=region.keyword_rules),
        schools=[
            RegionSchoolOut(
                id=school.id,
                atpt_ofcdc_sc_code=school.atpt_ofcdc_sc_code,
                sd_schul_code=school.sd_schul_code,
                school_name=school.school_name,
                school_level=school.school_level,
                address=school.address,
                display_order=school.display_order,
                is_active=school.is_active,
            )
            for school in schools
        ],
    )
    return data.model_dump()


@router.post("/regions/{region_id}/schools/auto-discover")
async def auto_discover_region_schools(region_id: int, db: Session = Depends(get_db)):
    service = RegionService(db)
    if not service.get_region(region_id):
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다.")
    candidates = await service.auto_discover_candidates(region_id)
    return {"region_id": region_id, "candidates": candidates}


@router.post("/regions/{region_id}/schools")
async def register_region_schools(region_id: int, payload: RegionSchoolRegisterRequest, db: Session = Depends(get_db)):
    service = RegionService(db)
    try:
        saved = service.register_region_schools(region_id, payload.schools)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "region_id": region_id,
        "schools": [
            RegionSchoolOut(
                id=school.id,
                atpt_ofcdc_sc_code=school.atpt_ofcdc_sc_code,
                sd_schul_code=school.sd_schul_code,
                school_name=school.school_name,
                school_level=school.school_level,
                address=school.address,
                display_order=school.display_order,
                is_active=school.is_active,
            ).model_dump()
            for school in saved
        ],
    }


@router.delete("/regions/{region_id}/schools/{school_id}")
async def deactivate_region_school(region_id: int, school_id: int, db: Session = Depends(get_db)):
    if not RegionService(db).deactivate_region_school(region_id, school_id):
        raise HTTPException(status_code=404, detail="지역 학교를 찾을 수 없습니다.")
    return {"ok": True}


@router.delete("/regions/{region_id}")
async def delete_region(region_id: int, db: Session = Depends(get_db)):
    if not RegionService(db).delete_region(region_id):
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다.")
    return {"ok": True}


@router.get("/regions/{region_id}/overview")
async def region_overview(region_id: int, target_date: str | None = None, db: Session = Depends(get_db)):
    service = RegionService(db)
    try:
        resolved_date = date.fromisoformat(target_date) if target_date else today_kst()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="target_date는 YYYY-MM-DD 형식이어야 합니다.") from exc
    try:
        overview = await service.get_region_overview(region_id, resolved_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return overview


@router.get("/regions/{region_id}/meals")
async def region_meals(region_id: int, target_date: str | None = None, db: Session = Depends(get_db)):
    service = RegionService(db)
    try:
        resolved_date = date.fromisoformat(target_date) if target_date else today_kst()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="target_date는 YYYY-MM-DD 형식이어야 합니다.") from exc
    try:
        data = await service.get_region_meals(region_id, resolved_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return data


@router.get("/regions/{region_id}/schedules")
async def region_schedules(
    region_id: int,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
):
    service = RegionService(db)
    try:
        start_date = date.fromisoformat(from_) if from_ else today_kst()
        end_date = date.fromisoformat(to) if to else start_date + timedelta(days=7)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="from/to는 YYYY-MM-DD 형식이어야 합니다.") from exc
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="to 날짜는 from 날짜보다 빠를 수 없습니다.")
    try:
        data = await service.get_region_schedules(region_id, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return data
