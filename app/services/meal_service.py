from __future__ import annotations

import re
from datetime import date

from sqlalchemy.orm import Session

from app.models import StudentProfile
from app.schemas import MealDetailItem, MealEntry, MealMenuItem
from app.services.neis_client import NeisClient
from app.utils import ALLERGY_CODE_MAP, parse_neis_date


ALLERGY_PATTERN = re.compile(r"\(([\d\.\s]+)\)")
BREAK_PATTERN = re.compile(r"<br\s*/?>", re.IGNORECASE)


class MealService:
    def __init__(self, db: Session):
        self.db = db
        self.client = NeisClient(db)

    @staticmethod
    def _normalize_breaks(raw_text: str | None) -> list[str]:
        if not raw_text:
            return []
        normalized = BREAK_PATTERN.sub("\n", str(raw_text))
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        return [line.strip() for line in normalized.split("\n") if line and line.strip()]

    @staticmethod
    def _split_menu(raw_menu: str) -> list[MealMenuItem]:
        items: list[MealMenuItem] = []
        for line in MealService._normalize_breaks(raw_menu):
            codes = []
            match = ALLERGY_PATTERN.search(line)
            if match:
                codes = [code.strip() for code in match.group(1).split(".") if code.strip()]
            name = ALLERGY_PATTERN.sub("", line).strip()
            items.append(
                MealMenuItem(
                    name=name,
                    allergy_codes=codes,
                    allergy_names=[ALLERGY_CODE_MAP.get(code, code) for code in codes],
                )
            )
        return items

    @staticmethod
    def _parse_detail_items(raw_text: str | None) -> tuple[list[str], list[MealDetailItem]]:
        lines = MealService._normalize_breaks(raw_text)
        items: list[MealDetailItem] = []
        for line in lines:
            if ":" in line:
                label, value = line.split(":", 1)
                items.append(MealDetailItem(label=label.strip(), value=value.strip()))
            elif " - " in line:
                label, value = line.split(" - ", 1)
                items.append(MealDetailItem(label=label.strip(), value=value.strip()))
        return lines, items

    @staticmethod
    def _collect_allergy_codes(menu_items: list[MealMenuItem]) -> list[str]:
        codes = {code for item in menu_items for code in item.allergy_codes}
        return sorted(codes, key=lambda value: int(value) if value.isdigit() else value)

    def _warning_list(self, profile: StudentProfile, menu_items: list[MealMenuItem]) -> list[str]:
        preferred_codes = {item.allergy_code for item in profile.allergies}
        preferred_names = {item.allergy_name for item in profile.allergies}
        warnings = set()
        for item in menu_items:
            codes = set(item.allergy_codes)
            names = set(item.allergy_names)
            for matched in sorted((preferred_codes & codes) | (preferred_names & names)):
                warnings.add(ALLERGY_CODE_MAP.get(matched, matched))
        return sorted(warnings)

    async def get_meals(self, profile: StudentProfile, start_date: date, end_date: date, force_refresh: bool = False) -> list[MealEntry]:
        rows = await self.client.get_dataset_rows(
            "mealServiceDietInfo",
            {
                "ATPT_OFCDC_SC_CODE": profile.atpt_ofcdc_sc_code,
                "SD_SCHUL_CODE": profile.sd_schul_code,
                "MLSV_FROM_YMD": start_date.strftime("%Y%m%d"),
                "MLSV_TO_YMD": end_date.strftime("%Y%m%d"),
            },
            force_refresh=force_refresh,
        )
        results: list[MealEntry] = []
        for row in rows:
            target = parse_neis_date(row.get("MLSV_YMD"))
            if not target:
                continue
            menu_items = self._split_menu(row.get("DDISH_NM", ""))
            origin_lines, _origin_items = self._parse_detail_items(row.get("ORPLC_INFO"))
            nutrition_lines, nutrition_items = self._parse_detail_items(row.get("NTR_INFO"))
            results.append(
                MealEntry(
                    date=target,
                    meal_name=str(row.get("MMEAL_SC_NM", "급식")).strip(),
                    menu_items=menu_items,
                    menu_summary=[item.name for item in menu_items[:3]],
                    calories=str(row.get("CAL_INFO", "")).strip() or None,
                    nutrition_lines=nutrition_lines,
                    nutrition_items=nutrition_items,
                    origin_lines=origin_lines,
                    allergy_warnings=self._warning_list(profile, menu_items),
                    allergy_codes=self._collect_allergy_codes(menu_items),
                )
            )
        results.sort(key=lambda item: item.date)
        return results
