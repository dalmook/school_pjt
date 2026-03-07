from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


settings = get_settings()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_sqlite_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        if "username" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(50)"))
        if "password_hash" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_unique ON users(username)"))
