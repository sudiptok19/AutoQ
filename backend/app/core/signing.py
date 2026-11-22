"""Signing-key custody (Opus). The private seed lives in a file outside the repo/DB
(AUTOQ_SIGNING_KEY_PATH, created by scripts/bootstrap_keys.py). Only public metadata is stored in
`signing_keys`. AQ1 sign/verify primitives belong to Astra's passport_crypto module."""
import base64
from datetime import UTC, datetime, timedelta

from nacl.signing import SigningKey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.db import models as m


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_signing_key() -> tuple[str, SigningKey]:
    p = settings().autoq_signing_key_path
    try:
        seed = base64.urlsafe_b64decode(p.read_text().strip() + "==")
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        return settings().autoq_signing_kid, SigningKey(seed)
    except (OSError, ValueError) as e:
        raise ApiError(503, "SIGNING_KEY_UNAVAILABLE", "Signing key unavailable — issuance aborted", {"component": "signing", "remediation": "Run `uv run python scripts/bootstrap_keys.py`.", "cause": type(e).__name__})


def register_active_key(db: Session) -> m.SigningKey:
    kid, sk = load_signing_key()
    pub = b64url(bytes(sk.verify_key))
    now = datetime.now(UTC)
    db.execute(insert(m.SigningKey).values(kid=kid, public_key=pub, status="ACTIVE", not_before=now - timedelta(days=1), not_after=now + timedelta(days=365)).on_conflict_do_nothing(index_elements=["kid"]))
    row = db.get(m.SigningKey, kid)
    if row.public_key != pub:
        raise ApiError(503, "SIGNING_KEY_MISMATCH", f"Key file does not match registered public key for {kid}", {"component": "signing"})
    return row


def list_public_keys(db: Session) -> list[dict]:
    return [{"kid": k.kid, "alg": k.alg, "public_key": k.public_key, "status": k.status, "not_before": int(k.not_before.timestamp()), "not_after": int(k.not_after.timestamp())}
            for k in db.scalars(select(m.SigningKey).order_by(m.SigningKey.kid))]
