"""Read-only SPC series for the line context supplied by the persistence port."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal
from app.db.session import get_db
from app.features.spc.service import compute_series
from app.ports import Ports, ports

router = APIRouter(tags=["spc"])


@router.get("/spc/series")
def get_series(
    part_id: Annotated[str, Query(min_length=1)],
    line_id: Annotated[str, Query(min_length=1)],
    method_version: Annotated[str, Query(min_length=1)],
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    persistence: Ports = Depends(ports),
) -> dict:
    points, baseline, rule, alerts = persistence.load_spc_inputs(db, part_id, line_id, method_version, principal)
    series = compute_series(points, baseline, rule)
    return {
        "part_id": part_id,
        "line_id": line_id,
        "method_version": method_version,
        "baseline": series.baseline,
        "rule": series.rule,
        "points": series.points,
        "alerts": alerts,
    }
