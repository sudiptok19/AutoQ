"""Authenticated inspection profile reads and golden-reference enrollment."""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.db.session import get_db
from app.features.component_vision.service import MAX_IMAGE_BYTES, validate_reference
from app.ports import Ports, VisionProfile, ports

router = APIRouter(tags=["component-vision"])


def _profile(profile: VisionProfile) -> dict:
    return {
        "id": profile.id,
        "part_id": profile.part_id,
        "station_id": profile.station_id,
        "method_version": profile.method_version,
        "calibrated": profile.calibrated,
        "calibration_version": profile.calibration_version,
        "reference_sha256": profile.reference_sha256,
    }


@router.get("/inspection-profiles")
def list_profiles(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    persistence: Ports = Depends(ports),
) -> dict:
    return {"items": [_profile(profile) for profile in persistence.list_profiles(db, principal)], "next_cursor": None}


@router.post("/inspection-profiles/{profile_id}/golden")
def upload_golden(
    profile_id: str,
    image: UploadFile = File(...),
    principal: Principal = Depends(require_roles("quality_engineer", "quality_manager")),
    db: Session = Depends(get_db),
    persistence: Ports = Depends(ports),
) -> dict:
    profile = persistence.load_profile(db, profile_id, principal)
    data = image.file.read(MAX_IMAGE_BYTES + 1)
    media_type = validate_reference(data, profile.thresholds, image.content_type)
    saved = persistence.save_reference(db, profile_id, data, media_type, principal)
    if saved.calibrated is not False:
        raise ApiError(503, "VISION_PROFILE_INVALID", "Golden upload did not produce an uncalibrated revision", {
            "component": "component_vision", "remediation": "Check the inspection profile persistence adapter.",
        })
    return _profile(saved)
