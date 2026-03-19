import os
from typing import List

from pydantic_settings import BaseSettings


def _coerce_db_url(url: str) -> str:
    """Heroku sets DATABASE_URL as postgres:// — convert to postgresql+asyncpg://"""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Settings(BaseSettings):
    PROJECT_NAME: str = "Finally API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Comma-separated origins in env: ALLOWED_ORIGINS_CSV=https://app.netlify.app,https://www.app.com
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:3000",
        "https://trdlybackend-323da282cc04.herokuapp.com",
        "https://algotradex-1feb03ba74fa.herokuapp.com",
    ]

    DATABASE_URL: str = "postgresql+asyncpg://localhost/app"

    model_config = {"env_file": ".env", "case_sensitive": True}

    def model_post_init(self, __context: object) -> None:
        object.__setattr__(self, "DATABASE_URL", _coerce_db_url(self.DATABASE_URL))
        # Allow ALLOWED_ORIGINS override via comma-separated env var
        raw = os.environ.get("ALLOWED_ORIGINS_CSV")
        if raw:
            object.__setattr__(self, "ALLOWED_ORIGINS", [o.strip() for o in raw.split(",")])


settings = Settings()
