"""Application settings — L00/L01."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_repo_root() -> Path:
    """Local: prodavan/; Docker image: /app (src lives at /app/src)."""
    here = Path(__file__).resolve()
    parents = here.parents
    if len(parents) > 5 and (parents[5] / "apps" / "api").is_dir():
        return parents[5]
    if len(parents) > 3:
        return parents[3]
    return Path("/app")


_REPO_ROOT = _default_repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan"
    cors_origins: str = (
        "http://localhost:3000,http://localhost:8080,http://localhost:5173,"
        "http://127.0.0.1:8080,http://prodavan.local,http://api.prodavan.local"
    )
    api_v1_prefix: str = "/api/v1"
    storage_root: Path = _REPO_ROOT / "data" / "storage"

    app_name: str = "prodavan-api"
    app_version: str = "0.1.0-l08"
    build_id: str = "dev"

    app_env: str = "dev"  # dev | prod — prod forbids AUTH_MODE=test

    # L01 OIDC
    # auth_mode: oidc (JWKS) | test (HS256 AUTH_TEST_SECRET — CI/local only)
    auth_mode: str = "test"
    keycloak_issuer_url: str | None = None
    oidc_jwks_url: str | None = None
    oidc_audience: str = "prodavan-api"
    oidc_jwks_cache_seconds: int = 300
    auth_test_secret: str = "dev-only-test-secret-change-me"

    # L01 Keycloak Admin invite (fake | admin)
    keycloak_invite_mode: str = "fake"
    keycloak_url: str | None = None
    keycloak_realm: str = "prodavan"
    keycloak_admin_client_id: str | None = None
    keycloak_admin_client_secret: str | None = None

    vault_addr: str | None = None
    secrets_dir: Path = _REPO_ROOT / "data" / "secrets"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
