from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.db.session import get_db
from app.features.recall import service as s

router = APIRouter(tags=["trace", "recall"])


class Scope(BaseModel):
    type: Literal["HEAT", "LOT", "UNIT", "PACKAGE"] | None = None
    id: str | None = Field(None, max_length=80)
    code: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def _one(self):
        if not (self.id or self.code):
            raise ValueError("scope needs id or code")
        return self


class PreviewIn(BaseModel):
    scope: Scope


class RecallIn(BaseModel):
    scope: Scope
    reason: str = Field(min_length=1, max_length=2000)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/trace")
def trace(q: str | None = None, heat: str | None = None, lot: str | None = None, unit: str | None = None, package: str | None = None,
          serial: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    given = [(t, v) for t, v in (("HEAT", heat), ("LOT", lot), ("UNIT", unit), ("PACKAGE", package), ("UNIT", serial), (None, q)) if v]
    if len(given) != 1:
        raise ApiError(422, "VALIDATION", "Provide exactly one of q, heat, lot, unit, package, serial")
    t, v = given[0]
    return s.trace(db, v, t)


@router.post("/recalls/preview")
def preview(body: PreviewIn, db: Session = Depends(get_db), _: Principal = Depends(require_roles("quality_engineer", "quality_manager"))) -> dict:
    return s.preview(db, body.scope.type, body.scope.id or body.scope.code)


@router.post("/recalls", status_code=201)
def execute(body: RecallIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    return s.execute(db, p, body.scope.type, body.scope.id or body.scope.code, body.reason)


@router.get("/recalls")
def recalls(db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return {"items": s.list_(db), "next_cursor": None}


@router.get("/recalls/{recall_id}")
def recall(recall_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return s.get(db, recall_id)


@router.post("/recalls/{recall_id}/release")
def release(recall_id: str, body: ReasonIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    return s.release(db, p, recall_id, body.reason)
