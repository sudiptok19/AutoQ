from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require_roles
from app.core.errors import ApiError
from app.core.storage import sniff
from app.db.session import get_db
from app.features.seal_vision.service import MAX_IMAGE_BYTES, compare, validate_evidence_source
from app.ports import Ports, ports

router = APIRouter(tags=["seal-vision"])


@router.post("/packages/{package_id}/seal-checks")
async def seal_check(
    package_id: str,
    image: UploadFile = File(),
    evidence_source: str = Form("LIVE_CAPTURE"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_roles("dispatcher", "quality_engineer", "quality_manager")),
    adapters: Ports = Depends(ports),
) -> dict:
    validate_evidence_source(evidence_source)
    probe = await image.read(MAX_IMAGE_BYTES + 1)
    if len(probe) > MAX_IMAGE_BYTES:
        raise ApiError(422, "INVALID_IMAGE", "The seal image exceeds the 10 MiB limit.",
                       {"component": "seal_vision", "remediation": "Upload a JPEG or PNG that is at most 10 MiB."})
    media_type = sniff(probe)
    if media_type not in ("image/jpeg", "image/png") or image.content_type not in (None, media_type):
        raise ApiError(422, "INVALID_IMAGE", "The upload must be a JPEG or PNG with a matching media type.",
                       {"component": "seal_vision", "remediation": "Capture and upload a new JPEG or PNG seal image."})

    def check_and_record() -> dict:
        ctx = adapters.load_seal_context(db, package_id, principal)
        evidence = compare(ctx.reference_bytes, probe, ctx.profile, evidence_source=evidence_source)
        return adapters.record_recheck(db, ctx, evidence, probe, media_type, evidence_source, principal)

    return await run_in_threadpool(check_and_record)
