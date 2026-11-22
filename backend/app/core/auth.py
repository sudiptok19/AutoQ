"""JWT bearer auth + role checks. Roles are re-read from the database on every request,
so revoking a role takes effect immediately (the token only proves identity)."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import get_db

_ph = PasswordHasher()
ALG = "HS256"


@dataclass(frozen=True)
class Principal:
    id: str
    username: str
    display_name: str
    roles: tuple[str, ...]

    def has(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "roles": list(self.roles)}


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def check_password(stored: str, pw: str) -> bool:
    try:
        return _ph.verify(stored, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _secret() -> str:
    s = settings().autoq_jwt_secret
    if len(s.encode()) < 32:
        raise ApiError(503, "AUTH_MISCONFIGURED", "AUTOQ_JWT_SECRET must be at least 32 bytes", {"component": "auth"})
    return s


def issue_token(user_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + timedelta(minutes=settings().autoq_jwt_ttl_minutes)}, _secret(), algorithm=ALG)


def load_principal(db: Session, user_id: str) -> Principal | None:
    u = db.get(m.User, user_id)
    if not u or not u.active:
        return None
    roles = tuple(sorted(db.scalars(select(m.UserRole.role).where(m.UserRole.user_id == u.id))))
    return Principal(u.id, u.username, u.display_name, roles)


def principal_from_token(db: Session, token: str) -> Principal:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALG], options={"require": ["sub", "exp"]})
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED", "Session expired — sign in again")
    except jwt.PyJWTError:
        raise ApiError(401, "UNAUTHENTICATED", "Invalid bearer token")
    p = load_principal(db, claims["sub"])
    if not p:
        raise ApiError(401, "UNAUTHENTICATED", "User no longer active")
    return p


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "Sign in required")
    return principal_from_token(db, h[7:].strip())


def require_roles(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if roles and not p.has(*roles):
            raise ApiError(403, "ROLE_FORBIDDEN", f"Requires role: {' or '.join(roles)}", {"required": list(roles), "have": list(p.roles)})
        return p
    return dep
