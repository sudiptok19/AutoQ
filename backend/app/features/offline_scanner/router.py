from fastapi import APIRouter, Depends, Query
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_roles
from app.core.errors import ApiError
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
    # Device-chain version this event was hashed under (absent = 1). v2 binds chain_version and snapshot_id into the
    # hash (snapshot_id: exact capture snapshot for offline decisions, explicit null for ONLINE). Readers accept both.
    chain_version: Literal[1, 2] = 1

    @field_validator("scanned_at")
    @classmethod
    def _aware(cls, v: str) -> str:
        try:
            s.parse_capture_time(v)
        except ApiError as e:
            raise ValueError(e.message)
        return v


class SyncIn(BaseModel):
    device_id: str = Field(pattern=DEVICE)
    snapshot_id: str | None = None
    events: list[EventIn] = Field(max_length=500)


@router.get("/offline/snapshot")
def snapshot(shipment_id: str, device_id: str = Query(pattern=DEVICE), local_seq: int | None = Query(None, ge=0),
             local_head: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"), pending: int | None = Query(None, ge=0),
             db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    """`local_seq`/`local_head`/`pending` let a device with an empty queue at the server head settle its OLDER
    snapshots for this shipment while refreshing (never the one being issued)."""
    return s.snapshot(db, p, shipment_id, device_id, local_seq=local_seq, local_head=local_head, pending=pending)


@router.post("/sync/scans")
def sync(body: SyncIn, db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    return s.sync(db, p, body.device_id, body.snapshot_id, [_event_dict(e) for e in body.events])


def _event_dict(e: EventIn) -> dict:
    """Keep the difference between an absent snapshot_id and an explicit null: under chain v2 the field is hashed, and
    'absent' is a schema error, not 'no snapshot'."""
    d = e.model_dump()
    if "snapshot_id" not in e.model_fields_set:
        del d["snapshot_id"]
    return d


HEX64 = r"^[0-9a-f]{64}$"


class SettleIn(BaseModel):
    device_id: str = Field(pattern=DEVICE)
    local_seq: int = Field(ge=0)
    local_head: str = Field(pattern=HEX64)     # settlement binds the terminal sequence AND hash
    pending: int = Field(ge=0)


class RecoverIn(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    # Uploaded physical reconciliation records (POST /evidence). Without them, recovery raises a reconciliation task
    # and the shipment stays blocked until that task is completed.
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


@router.post("/offline/snapshots/{snapshot_id}/settle")
def settle(snapshot_id: str, body: SettleIn, db: Session = Depends(get_db), p: Principal = Dispatcher) -> dict:
    """End an offline session: the device hands back its authorization (requires an empty queue at the server head)."""
    return s.settle(db, p, snapshot_id, body.device_id, body.local_seq, body.local_head, body.pending)


@router.post("/offline/snapshots/{snapshot_id}/recover")
def recover(snapshot_id: str, body: RecoverIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    """Lost or unavailable device: settle its authorization explicitly, with an audited reason (Quality Manager)."""
    return s.recover(db, p, snapshot_id, body.reason, body.evidence_ids)
