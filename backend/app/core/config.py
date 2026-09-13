from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    APP_NAME: str = "ScholarSync"
    DEBUG: bool = True
    SECRET_KEY: str = "changeme-in-production-use-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    DATABASE_URL: str = "postgresql://scholarsync:scholarsync@localhost/scholarsync"
    MEILISEARCH_URL: str = "http://localhost:7700"
    MEILISEARCH_KEY: str = ""

    ALLOWED_LOGO_TYPES: List[str] = ["image/png", "image/svg+xml", "image/jpeg"]
    MAX_LOGO_SIZE_MB: int = 2
    UPLOAD_DIR: str = "static/img/uploads"

    SYNC_INTERVAL_MINUTES: int = 60

    class Config:
        env_file = ".env"

settings = Settings()
