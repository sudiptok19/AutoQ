from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.signing import load_signing_key


def health(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except OperationalError:
        raise ApiError(503, "DB_UNAVAILABLE", "Database unavailable", {"component": "postgres"})
    kid, _ = load_signing_key()  # raises 503 SIGNING_KEY_UNAVAILABLE
    return {"status": "ok", "db": "ok", "key_kid": kid, "version": settings().app_version, "demo": settings().autoq_demo}
