from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import NeisCache
from app.schemas import ClassInfoResult, SchoolSearchResult
from app.utils import now_kst, parse_neis_date, stable_hash


logger = logging.getLogger(__name__)


DATASET_NAMES = {
    "schoolInfo": "학교기본정보",
    "classInfo": "학급정보",
    "mealServiceDietInfo": "급식식단정보",
    "SchoolSchedule": "학사일정",
    "elsTimetable": "초등학교시간표",
    "misTimetable": "중학교시간표",
    "hisTimetable": "고등학교시간표",
    "spsTimetable": "특수학교시간표",
}


class NeisClient:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.base_url = "https://open.neis.go.kr/hub"

    async def _request(self, dataset: str, params: dict[str, Any], force_refresh: bool = False) -> dict[str, Any]:
        query = {"KEY": self.settings.neis_api_key, "Type": "json", "pIndex": 1, "pSize": 1000, **params}
        url = f"{self.base_url}/{dataset}?{urlencode(query, doseq=True)}"
        cache_key = stable_hash({"dataset": dataset, "query": query})
        target_date = None
        for key in ("MLSV_YMD", "AA_YMD", "ALL_TI_YMD", "TI_FROM_YMD", "TI_TO_YMD"):
            if key in params:
                try:
                    target_date = parse_neis_date(str(params[key]))
                except Exception:
                    target_date = None
                break

        cached = None if force_refresh else self.db.scalar(
            select(NeisCache).where(NeisCache.cache_key == cache_key, NeisCache.expires_at > datetime.utcnow())
        )
        if cached:
            return json.loads(cached.payload_json)

        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            last_error: Exception | None = None
            for attempt in range(1, self.settings.request_max_retries + 1):
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                    self._save_cache(cache_key, dataset, target_date, payload, params)
                    return payload
                except Exception as exc:
                    last_error = exc
                    logger.warning("NEIS request failed dataset=%s attempt=%s error=%s", dataset, attempt, exc)
                    await asyncio.sleep(0.5 * attempt)
            raise RuntimeError(f"NEIS API 호출 실패: {DATASET_NAMES.get(dataset, dataset)}") from last_error

    def _save_cache(self, cache_key: str, dataset: str, target_date: date | None, payload: dict[str, Any], params: dict[str, Any]) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False)
        expires_at = datetime.utcnow() + self._cache_ttl(dataset, params)
        cache_row = self.db.scalar(select(NeisCache).where(NeisCache.cache_key == cache_key))
        if cache_row:
            cache_row.payload_json = payload_json
            cache_row.payload_hash = stable_hash(payload)
            cache_row.expires_at = expires_at
        else:
            self.db.add(
                NeisCache(
                    cache_key=cache_key,
                    endpoint_name=dataset,
                    target_date=target_date,
                    payload_json=payload_json,
                    payload_hash=stable_hash(payload),
                    expires_at=expires_at,
                )
            )
        self.db.commit()

    @staticmethod
    def extract_rows(dataset: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if "RESULT" in payload:
            code = payload["RESULT"].get("CODE")
            if code == "INFO-200":
                return []
            if code and code != "INFO-000":
                raise RuntimeError(payload["RESULT"].get("MESSAGE", "NEIS API 오류"))
        section = payload.get(dataset)
        if not section:
            return []
        for item in section:
            if isinstance(item, dict) and "row" in item:
                return item["row"]
        return []

    async def search_schools(self, query: str, force_refresh: bool = False) -> list[SchoolSearchResult]:
        payload = await self._request("schoolInfo", {"SCHUL_NM": query}, force_refresh=force_refresh)
        rows = self.extract_rows("schoolInfo", payload)
        results: list[SchoolSearchResult] = []
        for row in rows[:20]:
            results.append(
                SchoolSearchResult(
                    atpt_ofcdc_sc_code=row.get("ATPT_OFCDC_SC_CODE", ""),
                    sd_schul_code=row.get("SD_SCHUL_CODE", ""),
                    school_name=row.get("SCHUL_NM", ""),
                    school_level=row.get("SCHUL_KND_SC_NM", ""),
                    org_name=str(row.get("ATPT_OFCDC_SC_NM", "")).strip() or str(row.get("JU_ORG_NM", "")).strip() or None,
                    location_summary=self._location_summary(row),
                    address=" ".join(filter(None, [row.get("ORG_RDNMA"), row.get("ORG_RDNDA")])),
                    tel=row.get("ORG_TELNO"),
                    homepage=row.get("HMPG_ADRES"),
                    coedu=row.get("COEDU_SC_NM"),
                    fond_date=row.get("FOND_YMD"),
                )
            )
        return results

    async def get_school_info(self, atpt_code: str, school_code: str, force_refresh: bool = False) -> SchoolSearchResult | None:
        payload = await self._request("schoolInfo", {"ATPT_OFCDC_SC_CODE": atpt_code, "SD_SCHUL_CODE": school_code}, force_refresh=force_refresh)
        rows = self.extract_rows("schoolInfo", payload)
        if not rows:
            return None
        row = rows[0]
        return SchoolSearchResult(
            atpt_ofcdc_sc_code=row.get("ATPT_OFCDC_SC_CODE", ""),
            sd_schul_code=row.get("SD_SCHUL_CODE", ""),
            school_name=row.get("SCHUL_NM", ""),
            school_level=row.get("SCHUL_KND_SC_NM", ""),
            org_name=str(row.get("ATPT_OFCDC_SC_NM", "")).strip() or str(row.get("JU_ORG_NM", "")).strip() or None,
            location_summary=self._location_summary(row),
            address=" ".join(filter(None, [row.get("ORG_RDNMA"), row.get("ORG_RDNDA")])),
            tel=row.get("ORG_TELNO"),
            homepage=row.get("HMPG_ADRES"),
            coedu=row.get("COEDU_SC_NM"),
            fond_date=row.get("FOND_YMD"),
        )

    async def get_classes(self, atpt_code: str, school_code: str, force_refresh: bool = False) -> list[ClassInfoResult]:
        payload = await self._request(
            "classInfo",
            {
                "ATPT_OFCDC_SC_CODE": atpt_code,
                "SD_SCHUL_CODE": school_code,
                "AY": str(self._current_school_year()),
            },
            force_refresh=force_refresh,
        )
        rows = self.extract_rows("classInfo", payload)
        deduped: dict[tuple[int, str], ClassInfoResult] = {}
        for row in rows:
            grade = int(str(row.get("GRADE", "0")).strip() or "0")
            class_nm = str(row.get("CLASS_NM", "")).strip()
            if grade and class_nm:
                deduped[(grade, class_nm)] = ClassInfoResult(grade=grade, class_nm=class_nm)
        return sorted(deduped.values(), key=self._class_sort_key)

    @staticmethod
    def _class_sort_key(item: ClassInfoResult) -> tuple[int, int, int | str]:
        class_nm = item.class_nm.strip()
        if class_nm.isdigit():
            return (item.grade, 0, int(class_nm))
        return (item.grade, 1, class_nm)

    @staticmethod
    def _current_school_year() -> int:
        today = now_kst().date()
        return today.year - 1 if today.month < 3 else today.year

    @staticmethod
    def _location_summary(row: dict[str, Any]) -> str | None:
        road = " ".join(filter(None, [row.get("ORG_RDNMA"), row.get("ORG_RDNDA")])).strip()
        if road:
            parts = [part for part in road.split() if part]
            if len(parts) >= 3:
                return " ".join(parts[:3])
            return road
        office = str(row.get("JU_ORG_NM", "")).strip()
        if office:
            return office
        region = str(row.get("LCTN_SC_NM", "")).strip()
        return region or None

    async def get_dataset_rows(self, dataset: str, params: dict[str, Any], force_refresh: bool = False) -> list[dict[str, Any]]:
        payload = await self._request(dataset, params, force_refresh=force_refresh)
        return self.extract_rows(dataset, payload)

    def _cache_ttl(self, dataset: str, params: dict[str, Any]) -> timedelta:
        if dataset == "schoolInfo":
            return timedelta(hours=self.settings.school_info_cache_hours)
        if dataset == "classInfo":
            return timedelta(hours=self.settings.class_info_cache_hours)
        if dataset == "mealServiceDietInfo":
            return timedelta(hours=self.settings.meal_cache_hours)
        if dataset == "SchoolSchedule":
            return timedelta(hours=self.settings.schedule_cache_hours)
        if dataset in {"elsTimetable", "misTimetable", "hisTimetable", "spsTimetable"}:
            return timedelta(hours=self.settings.timetable_cache_hours)
        return timedelta(minutes=self.settings.cache_ttl_minutes)
