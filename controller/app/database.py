from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.controller_db_url,
    connect_args={"check_same_thread": False} if settings.controller_db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
Base = declarative_base()


def ensure_schema() -> None:
    # Always ensure mapped tables exist before running column migrations
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        table_exists = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='agents';")
        ).fetchone()
        if not table_exists:
            return
        result = connection.execute(text("PRAGMA table_info(agents);"))
        columns = {row[1] for row in result}
        if "interfaces_json" not in columns:
            connection.execute(text("ALTER TABLE agents ADD COLUMN interfaces_json JSON"))
        if "monitor_map_json" not in columns:
            connection.execute(text("ALTER TABLE agents ADD COLUMN monitor_map_json JSON"))
        if "gps_json" not in columns:
            connection.execute(text("ALTER TABLE agents ADD COLUMN gps_json JSON"))


@contextmanager
def get_session() -> Session:
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
