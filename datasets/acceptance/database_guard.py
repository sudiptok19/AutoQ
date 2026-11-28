
"""Refuse direct test execution or database targets the runner did not create."""

import os
import re
from urllib.parse import urlsplit

import psycopg


def require_disposable_database():
    admin = os.environ.get("AUTOQ_TEST_ADMIN_URL", "")
    nonce = os.environ.get("AUTOQ_ACCEPTANCE_NONCE", "")
    parsed = urlsplit(admin)
    if (
        not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or parsed.scheme != "postgresql"
        or parsed.hostname != "127.0.0.1"
        or parsed.path != "/postgres"
        or not parsed.port
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Run through run.py with a fresh isolated PostgreSQL container")
    with psycopg.connect(admin, connect_timeout=3, options="-c default_transaction_read_only=on") as connection:
        if connection.execute("SELECT nonce FROM acceptance_guard").fetchall() != [(nonce,)]:
            raise RuntimeError("Disposable PostgreSQL marker mismatch")


if __name__ == "__main__":
    require_disposable_database()
