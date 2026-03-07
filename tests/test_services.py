from datetime import date

from app.services.meal_service import MealService
from app.services.neis_client import NeisClient
from app.services.notification_service import NotificationService
from app.schemas import ClassInfoResult
from app.utils import ALLERGY_CODE_MAP, blocked_timetable_period


class DummyAllergy:
    def __init__(self, code: str):
        self.allergy_code = code
        self.allergy_name = ALLERGY_CODE_MAP[code]


class DummySupply:
    def __init__(self, subject: str, text: str):
        self.subject_name = subject
        self.supply_text = text


class DummyPeriod:
    def __init__(self, subject: str):
        self.subject = subject


def test_blocked_timetable_period():
    assert blocked_timetable_period(date(2024, 9, 1)) is True
    assert blocked_timetable_period(date(2026, 3, 1)) is False


def test_meal_menu_parsing():
    items = MealService._split_menu("카레라이스(2.5.6)<br/>우유(2)")
    assert items[0].name == "카레라이스"
    assert items[0].allergy_codes == ["2", "5", "6"]


def test_supply_generation():
    class DummyProfile:
        supplies = [DummySupply("체육", "체육복"), DummySupply("음악", "리코더")]

    service = NotificationService(db=None)  # type: ignore[arg-type]
    supplies = service.build_supply_list(DummyProfile(), [DummyPeriod("체육"), DummyPeriod("음악"), DummyPeriod("체육")])
    assert supplies == ["체육복", "리코더"]


def test_class_sort_key_handles_numeric_and_named_classes():
    items = [
        ClassInfoResult(grade=3, class_nm="나"),
        ClassInfoResult(grade=3, class_nm="2"),
        ClassInfoResult(grade=3, class_nm="가"),
        ClassInfoResult(grade=3, class_nm="1"),
    ]
    sorted_items = sorted(items, key=NeisClient._class_sort_key)
    assert [item.class_nm for item in sorted_items] == ["1", "2", "가", "나"]
