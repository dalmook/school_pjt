from __future__ import annotations

import hashlib
import hmac
import secrets

from itsdangerous import URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.utils import new_web_key


SESSION_COOKIE = "school_alert_session"


def session_serializer() -> URLSafeSerializer:
    settings = get_settings()
    return URLSafeSerializer(settings.app_secret, salt="school-alert-session")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, digest = stored_hash.split("$", 1)
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, f"{salt}${digest}")


def create_user(db: Session, username: str, password: str) -> User:
    user = User(username=username.strip(), password_hash=hash_password(password), web_key=new_web_key())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def encode_session(user_id: int) -> str:
    return session_serializer().dumps({"user_id": user_id})


def decode_session(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = session_serializer().loads(token)
    except Exception:
        return None
    user_id = payload.get("user_id")
    return int(user_id) if user_id else None
