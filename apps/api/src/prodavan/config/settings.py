"""Application settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_repo_root() -> Path:
    """Local: prodavan/; Docker image: /app (src lives at /app/src)."""
    here = Path(__file__).resolve()
    parents = here.parents
    # .../prodavan/apps/api/src/prodavan/config/settings.py → parents[5] == prodavan
    if len(parents) > 5 and (parents[5] / "apps" / "api").is_dir():
        return parents[5]
    # /app/src/prodavan/config/settings.py → parents[3] == /app
    if len(parents) > 3:
        return parents[3]
    return Path("/app")


_REPO_ROOT = _default_repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan"
    jwt_secret: str = "dev-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_days: int = 30
    # Comma-separated; include Flutter web / Ingress origins in cluster.
    cors_origins: str = (
        "http://localhost:3000,http://localhost:8080,http://localhost:5173,"
        "http://127.0.0.1:8080,http://prodavan.local,http://api.prodavan.local"
    )
    api_v1_prefix: str = "/api/v1"
    # In k3s mount PVC at /data/storage and set STORAGE_ROOT=/data/storage
    storage_root: Path = _REPO_ROOT / "data" / "storage"
    packs_root: Path = _REPO_ROOT / "packages" / "cabinet-packs"
    s4b_base_url: str = "http://s4b.ru/s.jsp"
    s4b_timeout_seconds: float = 30.0
    s4b_cooldown_seconds: float = 10.0
    s4b_poll_attempts: int = 8
    s4b_poll_delay_seconds: float = 2.0
    # Bootstrap platform.admin (no public UI). Empty = skip seed.
    platform_admin_id: str = "platform-admin"
    platform_admin_password: str = "platform-admin-pass"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
