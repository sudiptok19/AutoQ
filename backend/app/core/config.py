from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

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


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}


def disposable_database_problem(url: str) -> str | None:
    """Why `url` is NOT safe to wipe for an automated end-to-end run, or None. Only a local database whose name
    ends in `_e2e` qualifies; dev, demo and physical-verification databases never do."""
    u = urlparse(url.replace("+psycopg", ""))
    name = u.path.lstrip("/")
    if (u.hostname or "") not in LOCAL_HOSTS:
        return f"host {u.hostname!r} is not local"
    if not name.endswith("_e2e") or not name.replace("_", "").isalnum():
        return f"database {name!r} is not a disposable e2e database (its name must end in _e2e)"
    return None
