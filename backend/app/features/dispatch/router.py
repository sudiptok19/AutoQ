from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import get_db
from app.features.dispatch import segregation
from app.features.dispatch import service as s

router = APIRouter(tags=["dispatch"])
Dispatcher = Depends(require_roles("dispatcher"))


class ShipmentIn(BaseModel):
    order_id: str
    vehicle_no: str = Field(min_length=1, max_length=32)
    dock: str = Field(min_length=1, max_length=32)


class ScanIn(BaseModel):
    token: str = Field(min_length=1, max_length=8192)
    client_event_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9:._\-]+$")
    device_id: str | None = Field(None, max_length=40)
    seal_check_id: str | None = None


class MoveIn(BaseModel):
    type: Literal["DEPARTED", "CHECKPOINT", "DELIVERED"]
    location: str = Field("", max_length=200)
    occurred_at: datetime | None = None


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/shipments")
def shipments(status: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    q = select(m.Shipment).order_by(m.Shipment.created_at.desc())
    if status:
        q = q.where(m.Shipment.status == status)
    return {"items": [s.shipment_view(db, x, scans=False) for x in db.scalars(q)], "next_cursor": None}


@router.get("/shipments/{key}")
def shipment(key: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return s.shipment_view(db, s.find_shipment(db, key))


@router.post("/shipments", status_code=201)
def add_shipment(body: ShipmentIn, db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    return s.create_shipment(db, p, body.order_id, body.vehicle_no, body.dock)


@router.post("/shipments/{key}/scans")
def scan(key: str, body: ScanIn, db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    # seal_check_id is informational: the gate always evaluates the latest check on the package's CURRENT revision
    view, _ = s.scan(db, p, key, body.token, body.client_event_id, body.device_id)
    return view


class CloseIn(BaseModel):
    unsynced_offline_events: int = Field(0, ge=0)


@router.post("/shipments/{key}/close")
def close(key: str, body: CloseIn | None = None, db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    return s.close(db, p, key, unsynced=(body.unsynced_offline_events if body else 0))


@router.post("/shipments/{key}/events")
def events(key: str, body: MoveIn, db: Session = Depends(get_db), p: Principal = Dispatcher):
    out, status = s.movement(db, p, key, body.type, body.location, body.occurred_at)
    return out if status == 200 else JSONResponse(out, status_code=status)  # 409 still commits the removal tasks


@router.get("/segregation-tasks")
def tasks(status: str | None = Query(None, pattern="^(OPEN|COMPLETED)$"), db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    q = select(m.SegregationTask).order_by(m.SegregationTask.created_at.desc())
    if status:
        q = q.where(m.SegregationTask.status == status)
    return {"items": [segregation.view(db, t) for t in db.scalars(q)], "next_cursor": None}


@router.post("/segregation-tasks/{task_id}/complete")
def complete(task_id: str, body: ReasonIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("dispatcher", "quality_engineer", "quality_manager"))) -> dict:
    return segregation.complete(db, p, task_id, body.reason)
