from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class SchoolSearchResult(BaseModel):
    atpt_ofcdc_sc_code: str
    sd_schul_code: str
    school_name: str
    school_level: str
    location_summary: str | None = None
    address: str | None = None
    tel: str | None = None
    homepage: str | None = None
    coedu: str | None = None
    fond_date: str | None = None


class ClassInfoResult(BaseModel):
    grade: int
    class_nm: str


class TimetablePeriod(BaseModel):
    period: str
    subject: str
    changed_from: str | None = None


class MealMenuItem(BaseModel):
    name: str
    allergy_codes: list[str] = Field(default_factory=list)
    allergy_names: list[str] = Field(default_factory=list)


class MealDetailItem(BaseModel):
    label: str
    value: str


class MealEntry(BaseModel):
    date: date
    meal_name: str
    menu_items: list[MealMenuItem] = Field(default_factory=list)
    menu_summary: list[str] = Field(default_factory=list)
    calories: str | None = None
    nutrition_lines: list[str] = Field(default_factory=list)
    nutrition_items: list[MealDetailItem] = Field(default_factory=list)
    origin_lines: list[str] = Field(default_factory=list)
    allergy_warnings: list[str] = Field(default_factory=list)
    allergy_codes: list[str] = Field(default_factory=list)


class ScheduleEntry(BaseModel):
    date: date
    event_name: str
    details: str | None = None
    badge: str
    badge_tone: str = "neutral"
    is_day_off: bool = False
    dday: int | None = None


class CalendarDay(BaseModel):
    date: date
    is_current_month: bool
    is_today: bool
    is_selected: bool
    events: list[ScheduleEntry] = Field(default_factory=list)
    hidden_count: int = 0
    href: str = ""


class EmptyState(BaseModel):
    title: str
    description: str
    action_label: str | None = None
    action_href: str | None = None
    tone: str = "neutral"


class ErrorState(BaseModel):
    title: str
    description: str
