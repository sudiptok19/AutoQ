"""Create a DISPOSABLE database for the Playwright suite, migrate it and play the demo story into it.

Safeguards (the e2e run must never touch a database someone cares about, e.g. a physical-verification one):
- The target is chosen here, never from `.env`: DATABASE_URL must name a database ending in `_e2e` on a local host.
- That database is dropped and recreated from scratch, so there is nothing in it to lose.
- The API that Playwright starts runs with AUTOQ_E2E=1, which makes it refuse to boot against any other database,
  and the demo reset refuses too (scripts/seed_demo.py).
- It signs with its own throwaway Ed25519 key (kid aq-e2e-1, created under e2e/.tmp), never the dev key.
Run by e2e/playwright.config.ts before it starts the API; not meant to be run by hand.
"""
import base64
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import disposable_database_problem  # noqa: E402


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if os.environ.get("AUTOQ_E2E") != "1":
        sys.exit("refusing: AUTOQ_E2E=1 is not set (this script is for the Playwright suite only)")
    if problem := disposable_database_problem(url):
        sys.exit(f"refusing to prepare {url!r}: {problem}")
    key = Path(os.environ.get("AUTOQ_SIGNING_KEY_PATH", ""))
    if os.environ.get("AUTOQ_SIGNING_KID") != "aq-e2e-1" or "e2e" not in str(key):
        sys.exit("refusing: the e2e API must sign with its own throwaway key (AUTOQ_SIGNING_KID=aq-e2e-1 under e2e/.tmp)")
    if not key.exists():   # isolated signing: never the dev key that physical-verification labels were signed with
        from nacl.signing import SigningKey
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(base64.urlsafe_b64encode(bytes(SigningKey.generate())).rstrip(b"=").decode())
    u = urlparse(url.replace("+psycopg", ""))
    name = u.path.lstrip("/")
    admin = u._replace(path="/postgres").geturl()
    with psycopg.connect(admin, autocommit=True, connect_timeout=3) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    from scripts import seed_demo
    sys.argv = ["seed_demo", "--reset", "--scanner-dock"]
    seed_demo.main()
    print(f"e2e database {name!r} ready")


if __name__ == "__main__":
    main()
