import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    controller_db_url: str = os.environ.get(
        "CONTROLLER_DB_URL",
        "sqlite:///" + os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "state", "controller.db")),
    )
    controller_base_url: str = os.environ.get("CONTROLLER_BASE_URL", "http://localhost:8000")
    elastic_url: str | None = os.environ.get("CONTROLLER_ELASTIC_URL")

    class Config:
        env_prefix = "CONTROLLER_"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
