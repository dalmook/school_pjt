from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    web_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    profiles: Mapped[list["StudentProfile"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    logs: Mapped[list["NotificationLog"]] = relationship(back_populates="user")


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    profile_name: Mapped[str] = mapped_column(String(100))
    atpt_ofcdc_sc_code: Mapped[str] = mapped_column(String(32))
    sd_schul_code: Mapped[str] = mapped_column(String(32), index=True)
    school_name: Mapped[str] = mapped_column(String(200))
    school_level: Mapped[str] = mapped_column(String(20))
    grade: Mapped[int] = mapped_column(Integer)
    class_nm: Mapped[str] = mapped_column(String(20))
    school_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    school_tel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    school_homepage: Mapped[str | None] = mapped_column(String(255), nullable=True)
    use_morning_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    use_evening_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    use_change_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    use_dday_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    use_meal_allergy_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    morning_alert_time: Mapped[str] = mapped_column(String(5), default="07:00")
    evening_alert_time: Mapped[str] = mapped_column(String(5), default="21:00")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="profiles")
    allergies: Mapped[list["AllergyPref"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    supplies: Mapped[list["SupplyRule"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    notification_logs: Mapped[list["NotificationLog"]] = relationship(back_populates="profile")
    timetable_snapshots: Mapped[list["TimetableSnapshot"]] = relationship(back_populates="profile")


class RegionGroup(Base):
    __tablename__ = "region_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    region_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    keyword_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    schools: Mapped[list["RegionSchool"]] = relationship(back_populates="region", cascade="all, delete-orphan")


class RegionSchool(Base):
    __tablename__ = "region_schools"
    __table_args__ = (
        UniqueConstraint("region_id", "atpt_ofcdc_sc_code", "sd_schul_code", name="uq_region_school_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("region_groups.id"), index=True)
    atpt_ofcdc_sc_code: Mapped[str] = mapped_column(String(32))
    sd_schul_code: Mapped[str] = mapped_column(String(32), index=True)
    school_name: Mapped[str] = mapped_column(String(200))
    school_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    region: Mapped["RegionGroup"] = relationship(back_populates="schools")


class AllergyPref(Base):
    __tablename__ = "allergy_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    allergy_code: Mapped[str] = mapped_column(String(20))
    allergy_name: Mapped[str] = mapped_column(String(50))

    profile: Mapped["StudentProfile"] = relationship(back_populates="allergies")


class SupplyRule(Base):
    __tablename__ = "supply_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    subject_name: Mapped[str] = mapped_column(String(100))
    supply_text: Mapped[str] = mapped_column(String(255))

    profile: Mapped["StudentProfile"] = relationship(back_populates="supplies")


class NeisCache(Base):
    __tablename__ = "neis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    endpoint_name: Mapped[str] = mapped_column(String(100), index=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(128))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    __table_args__ = (
        UniqueConstraint("profile_id", "notification_type", "target_date", "message_hash", name="uq_notification_dedupe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    notification_type: Mapped[str] = mapped_column(String(50), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    message_hash: Mapped[str] = mapped_column(String(128))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="logs")
    profile: Mapped["StudentProfile"] = relationship(back_populates="notification_logs")


class TimetableSnapshot(Base):
    __tablename__ = "timetable_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    raw_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    profile: Mapped["StudentProfile"] = relationship(back_populates="timetable_snapshots")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[str] = mapped_column(String(50), default="idle")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
