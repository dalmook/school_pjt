from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AllergyPref, StudentProfile, SupplyRule, TelegramSession, User
from app.utils import ALLERGY_CODE_MAP, new_web_key


def get_or_create_web_user(db: Session, web_key: str | None) -> User:
    if web_key:
        existing = db.scalar(select(User).where(User.web_key == web_key))
        if existing:
            return existing
    user = User(web_key=new_web_key())
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(TelegramSession(user_id=user.id))
    db.commit()
    return user


def get_or_create_telegram_user(db: Session, telegram_user_id: str, chat_id: str) -> User:
    user = db.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user:
        user.telegram_chat_id = chat_id
        db.commit()
        return user
    user = User(web_key=new_web_key(), telegram_user_id=telegram_user_id, telegram_chat_id=chat_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(TelegramSession(user_id=user.id))
    db.commit()
    return user


def get_telegram_session(db: Session, user_id: int) -> TelegramSession:
    session = db.scalar(select(TelegramSession).where(TelegramSession.user_id == user_id))
    if session:
        return session
    session = TelegramSession(user_id=user_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def replace_profile_rules(
    profile: StudentProfile,
    allergy_codes: list[str],
    allergy_names: list[str],
    supply_rules: list[tuple[str, str]],
) -> None:
    profile.allergies.clear()
    for code in allergy_codes:
        if code:
            profile.allergies.append(AllergyPref(allergy_code=code, allergy_name=ALLERGY_CODE_MAP.get(code, code)))
    profile.supplies.clear()
    for subject, text in supply_rules:
        if subject.strip() and text.strip():
            profile.supplies.append(SupplyRule(subject_name=subject.strip(), supply_text=text.strip()))
