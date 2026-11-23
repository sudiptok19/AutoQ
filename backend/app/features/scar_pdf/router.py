"""Authorized 8D PDF download and persistence through the report port."""

import re

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal
from app.db.session import get_db
from app.features.scar_pdf.service import render_pdf
from app.ports import Ports, ports

router = APIRouter(tags=["scar-pdf"])


def _filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value[:64]).strip(".") or "report"


@router.get("/reports/{report_id}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def download_pdf(
    report_id: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    persistence: Ports = Depends(ports),
) -> Response:
    snapshot = persistence.load_snapshot(db, report_id, principal)
    data = render_pdf(snapshot)
    persistence.record_pdf(db, snapshot, data, principal)
    filename = f"8D-{_filename_part(snapshot.id)}-{_filename_part(snapshot.revision_id)}.pdf"
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })
