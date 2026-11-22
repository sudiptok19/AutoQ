"""Integration tests run against a REAL PostgreSQL 16 (`make db`), in a dedicated `autoq_test` database."""
import base64
import os
from pathlib import Path

import psycopg
import pytest
from nacl.signing import SigningKey

ADMIN_URL = os.environ.get("AUTOQ_TEST_ADMIN_URL", "postgresql://autoq:autoq-dev@localhost:5433/postgres")
TEST_DB = "autoq_test"


@pytest.fixture(scope="session", autouse=True)
def _environment(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("autoq")
    key = tmp / "ed25519.key"
    key.write_text(base64.urlsafe_b64encode(bytes(SigningKey.generate())).rstrip(b"=").decode())
    os.environ.update({
        "DATABASE_URL": ADMIN_URL.replace("/postgres", f"/{TEST_DB}").replace("postgresql://", "postgresql+psycopg://"),
        "AUTOQ_EVIDENCE_DIR": str(tmp / "evidence"),
        "AUTOQ_SIGNING_KEY_PATH": str(key),
        "AUTOQ_SIGNING_KID": "aq-test-1",
        "AUTOQ_JWT_SECRET": "test-secret-0123456789abcdef0123456789",
    })
    from app.core.config import settings
    from app.db.session import engine, session_factory
    for f in (settings, engine, session_factory):
        f.cache_clear()
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as c:
            c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            c.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError as e:
        pytest.exit(f"PostgreSQL not reachable at {ADMIN_URL} — run `make db` first ({e})", returncode=2)
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    command.upgrade(cfg, "head")
    yield
    engine().dispose()


@pytest.fixture(autouse=True)
def clean_db():
    from sqlalchemy import text

    from app.db.models import Base
    from app.db.session import session_factory
    from scripts.seed_dev_users import seed
    with session_factory()() as db:
        db.execute(text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " RESTART IDENTITY CASCADE"))
        seed(db)
        db.commit()
    yield


@pytest.fixture
def db():
    from app.db.session import session_factory
    with session_factory()() as s:
        yield s


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def auth(client):
    cache: dict[str, dict] = {}

    def headers(username: str) -> dict:
        if username not in cache:
            r = client.post("/api/v1/auth/login", json={"username": username, "password": "autoq-dev"})
            assert r.status_code == 200, r.text
            cache[username] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[username]
    return headers
