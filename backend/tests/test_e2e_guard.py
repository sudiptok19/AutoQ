"""The Playwright suite wipes its database. These guards keep it from ever wiping one that matters."""
import pytest
from fastapi.testclient import TestClient

from app.core.config import disposable_database_problem


@pytest.mark.nodb
@pytest.mark.parametrize("url, ok", [
    ("postgresql+psycopg://autoq:x@localhost:5433/autoq_e2e", True),
    ("postgresql+psycopg://autoq:x@127.0.0.1:5433/sol_e2e", True),
    ("postgresql+psycopg://autoq:x@localhost:5433/autoq", False),            # the dev / demo database
    ("postgresql+psycopg://autoq:x@localhost:5433/autoq_physical", False),   # a physical-verification database
    ("postgresql+psycopg://autoq:x@192.168.29.2:5433/autoq_e2e", False),     # right name, another machine
    ("postgresql+psycopg://autoq:x@localhost:5433/x_e2e;drop", False),
])
def test_only_local_e2e_databases_are_disposable(url, ok):
    assert (disposable_database_problem(url) is None) is ok


def test_api_refuses_to_boot_in_e2e_mode_against_a_kept_database(monkeypatch):
    from app.main import create_app
    monkeypatch.setenv("AUTOQ_E2E", "1")          # the test database is `autoq_test`, which is not disposable-by-name
    monkeypatch.setenv("AUTOQ_DISABLE_RELAY", "1")
    with pytest.raises(RuntimeError, match="not disposable"):
        with TestClient(create_app()):
            pass
