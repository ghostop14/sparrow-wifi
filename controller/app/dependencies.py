from __future__ import annotations

from typing import Generator

from sqlalchemy.orm import Session

from .database import get_session


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session
