from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal, current_principal, evidence_principal, require_roles
from app.core.storage import get_evidence
from app.db.session import get_db
from app.features.auth_evidence import service

router = APIRouter(tags=["auth", "evidence", "audit"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    return service.login(db, body.username, body.password)


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return p.public()


@router.get("/evidence/{sha256}", response_class=Response, responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "application/pdf": {}}}})
def evidence(sha256: str = Path(pattern=r"^[0-9a-f]{64}$"), db: Session = Depends(get_db), _: Principal = Depends(evidence_principal)) -> Response:
    data, media_type = get_evidence(db, sha256)
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=31536000, immutable", "ETag": f'"{sha256}"', "X-Content-Type-Options": "nosniff"})


@router.get("/audit")
def list_audit(entity_id: str | None = None, limit: int = Query(100, ge=1, le=500), cursor: int | None = None, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return service.list_audit(db, entity_id, limit, cursor)


@router.get("/audit/verify")
def verify_audit(db: Session = Depends(get_db), _: Principal = Depends(require_roles("quality_manager", "admin"))) -> dict:
    return audit.verify(db)
