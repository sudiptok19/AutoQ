from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_roles
from app.db.session import get_db
from app.features.offline_scanner import service as s

router = APIRouter(tags=["offline"])
Dispatcher = Depends(require_roles("dispatcher"))
DEVICE = r"^[A-Za-z0-9._\-]{3,40}$"


class EventIn(BaseModel):
    client_event_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9:._\-]+$")
    seq: int = Field(ge=1)
    prev_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token: str = Field(min_length=1, max_length=8192)
    shipment_id: str
    scanned_at: str
    local_decision: str
    local_reasons: list[dict] = Field(default_factory=list)
    snapshot_id: str | None = Field(None, max_length=40)   # the snapshot this decision was made under (overrides the batch default)


class SyncIn(BaseModel):
    device_id: str = Field(pattern=DEVICE)
    snapshot_id: str | None = None
    events: list[EventIn] = Field(max_length=500)


@router.get("/offline/snapshot")
def snapshot(shipment_id: str, device_id: str = Query(pattern=DEVICE), local_seq: int | None = Query(None, ge=0), pending: int | None = Query(None, ge=0),
             db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    """`local_seq`/`pending` let a device with an empty queue check in while refreshing (see manifest close)."""
    return s.snapshot(db, p, shipment_id, device_id, local_seq=local_seq, pending=pending)


@router.post("/sync/scans")
def sync(body: SyncIn, db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    return s.sync(db, p, body.device_id, body.snapshot_id, [e.model_dump() for e in body.events])
