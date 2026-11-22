from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), env_prefix="", extra="ignore")

    database_url: str = "postgresql+psycopg://autoq:autoq-dev@localhost:5433/autoq"
    autoq_jwt_secret: str = "dev-only-insecure-jwt-secret-change-me-0001"
    autoq_jwt_ttl_minutes: int = 12 * 60
    autoq_evidence_dir: Path = ROOT / "backend" / "evidence"
    autoq_signing_key_path: Path = ROOT / "backend" / ".keys" / "ed25519-dev.key"
    autoq_signing_kid: str = "aq-dev-1"
    autoq_demo: bool = False
    cors_origins: str = "http://localhost:5173,https://localhost:5173"
    app_version: str = "0.1.0"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()
