"""Application settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[5]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan"
    jwt_secret: str = "dev-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_days: int = 30
    cors_origins: str = "http://localhost:3000,http://localhost:8080"
    api_v1_prefix: str = "/api/v1"
    storage_root: Path = _REPO_ROOT / "data" / "storage"
    packs_root: Path = _REPO_ROOT / "packages" / "cabinet-packs"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
