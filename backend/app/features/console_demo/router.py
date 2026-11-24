"""Control-room summary and SYNTHETIC demo fixtures. Owner: Opus.

`/dashboard/summary` is a read-only aggregate for the Overview screen. `/demo/fixtures/*` serves the
backend's own SYNTHETIC_TEST renders so that a browser "fixture" capture is the same render family the
demo profiles were calibrated on. Uploads of these images must carry evidence_source=SYNTHETIC_TEST
(the frontend client does this), and the UI badges them; they are never evidence about a physical part."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal
from app.db import models as m
from app.db.session import get_db
from app.features.console_demo.synthetic import render, render_seal
from app.features.dispatch.service import scan_view
from app.features.packaging.service import package_view
from app.policy import eligibility

router = APIRouter(tags=["console"])


@router.get("/dashboard/summary")
def summary(db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    by = {"UNINSPECTED": 0, "PASS": 0, "HOLD": 0, "REJECT": 0}
    for status, n in db.execute(select(m.Unit.quality_status, func.count()).group_by(m.Unit.quality_status)):
        by[status] = n
    shipped = set(db.scalars(select(m.PackageItem.unit_id).join(m.Load, m.Load.package_id == m.PackageItem.package_id)
                             .where(m.PackageItem.removed_at.is_(None), m.Load.removed_at.is_(None))))
    eligible = sum(1 for u in db.scalars(select(m.Unit).where(m.Unit.quality_status == "PASS"))
                   if u.id not in shipped and eligibility.compute(db, u).dispatch_eligible)
    pkgs = [package_view(db, p) for p in db.scalars(select(m.Package).where(m.Package.state.in_(("OPEN", "SEALED", "SEAL_REVIEW", "LOADED"))))]
    verdicts = dict(db.execute(select(m.Inspection.verdict, func.count()).group_by(m.Inspection.verdict)).all())
    total = sum(verdicts.values())
    recent = db.scalars(select(m.Scan).order_by(m.Scan.decided_at.desc()).limit(8))
    return {
        "units_by_status": by,
        "active_containments": db.scalar(select(func.count()).select_from(m.ContainmentRun).where(m.ContainmentRun.status != "CLEARED")),
        "held_packages": sum(1 for p in pkgs if p["held"] or p["state"] == "SEAL_REVIEW"),
        "open_shipments": db.scalar(select(func.count()).select_from(m.Shipment).where(m.Shipment.status == "OPEN")),
        "open_alerts": db.scalar(select(func.count()).select_from(m.Alert).where(m.Alert.status == "OPEN")),
        "recent_scans": [scan_view(db, s) for s in recent],
        "dispatch_eligible_units": eligible,
        "first_pass_yield": (verdicts.get("PASS", 0) / total) if total else None,
    }


PartKind = Literal["golden", "good", "defect", "blurry", "glare"]
SealKind = Literal["enroll", "intact", "torn", "bad_light"]


def _jpeg(data: bytes) -> Response:
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-AutoQ-Evidence-Source": "SYNTHETIC_TEST"})


@router.get("/demo/fixtures/part/{kind}", response_class=Response, responses={200: {"content": {"image/jpeg": {}}}})
def part_fixture(kind: PartKind, seed: int = Query(11, ge=0, le=1_000_000), _: Principal = Depends(current_principal)) -> Response:
    return _jpeg(render(kind, seed))


@router.get("/demo/fixtures/seal/{kind}", response_class=Response, responses={200: {"content": {"image/jpeg": {}}}})
def seal_fixture(kind: SealKind, seed: int = Query(3, ge=0, le=1_000_000), _: Principal = Depends(current_principal)) -> Response:
    return _jpeg(render_seal(kind, seed))
