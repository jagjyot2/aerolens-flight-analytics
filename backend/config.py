"""Application configuration via Pydantic Settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_prefix="AEROLENS_",
        env_file=str(PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    app_name: str = "AeroLens Flight Analytics"
    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'aerolens.db').as_posix()}"
    max_upload_mb: int = 100
    auto_seed_samples: bool = True
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    @property
    def sample_data_dir(self) -> Path:
        return PROJECT_ROOT / "sample_data"

    @property
    def frontend_dist_dir(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    @property
    def static_fallback_dir(self) -> Path:
        return BACKEND_DIR / "static"


settings = Settings()
