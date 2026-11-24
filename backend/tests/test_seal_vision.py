"""Seal comparison and physical evaluation reporting."""

import json
import sys
from dataclasses import replace
from io import BytesIO
from shutil import copyfile

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.core import errors
from app.core.auth import Principal, current_principal
from app.core.errors import ApiError
from app.db.session import get_db
from app.features.component_vision._jpeg import jpeg_frame
from app.features.seal_vision import service
from app.features.seal_vision.router import router
from app.ports import SealContext, SealProfile, ports
from scripts.evaluate_vision import ROOT, evaluate_set, seal_method
from tests.seal_fixtures import render_seal

pytestmark = pytest.mark.nodb
PROFILE = SealProfile(service.METHOD_VERSION)
SOURCE = "SYNTHETIC_TEST"
ROUTE = "/api/v1/packages/pkg_demo/seal-checks"


@pytest.fixture(scope="module")
def reference() -> bytes:
    return render_seal("enroll", 0)


@pytest.fixture(scope="module")
def phone_capture() -> bytes:
    return (ROOT / "fixtures" / "images" / "seal" / "enroll.jpg").read_bytes()


def _png(array: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", array)
    assert ok
    return encoded.tobytes()


def _compare(reference: bytes, probe: bytes, profile: SealProfile = PROFILE) -> service.SealEvidence:
    return service.compare(reference, probe, profile, evidence_source=SOURCE)


def _no_registration(evidence: service.SealEvidence, reason: str) -> None:
    assert evidence.result == "UNVERIFIABLE"
    assert evidence.evidence_source == SOURCE
    assert evidence.quality_gate["passed"] is False
    assert reason in evidence.quality_gate["failures"]
    assert evidence.metrics["ssim"] is None
    assert evidence.aligned_png is None and evidence.diff_png is None


@pytest.mark.parametrize(("kind", "seed", "expected"), [("enroll", 0, "INTACT"), ("intact", 2, "INTACT"), ("torn", 4, "SUSPICIOUS")])
def test_registered_capture(reference, kind, seed, expected):
    probe = render_seal(kind, seed)
    evidence = _compare(reference, probe)
    assert evidence == _compare(reference, probe)
    assert evidence.result == expected
    assert evidence.evidence_source == SOURCE and evidence.quality_gate["passed"]
    assert evidence.method_version == service.METHOD_VERSION
    assert evidence.metrics["inliers"] >= PROFILE.min_inliers
    assert evidence.metrics["ssim"] is not None
    assert evidence.aligned_png.startswith(b"\x89PNG") and evidence.diff_png.startswith(b"\x89PNG")
    if kind == "torn":
        assert evidence.metrics["min_region_ssim"] < evidence.metrics["thresholds"]["regional_suspicious_ssim"]


def test_physical_seal_report_records_unadmitted_enrollment(tmp_path):
    source = ROOT / "fixtures" / "images" / "seal" / "manifest.json"
    fixed_files = {"enroll.jpg", "intact_recapture.jpg", "torn.jpg", "repositioned.jpg", "bad_light.jpg"}
    images = [row for row in json.loads(source.read_text(encoding="utf-8"))["images"] if row["file"] in fixed_files]
    for row in images:
        original = source.parent / row["file"]
        target = tmp_path / row["file"]
        if row["label"] == "enroll":
            data = original.read_bytes()
            target.write_bytes(data[:len(data) // 2])
        else:
            copyfile(original, target)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"images": images}), encoding="utf-8")
    report = evaluate_set("seal", manifest, tmp_path / "report.json")

    assert report["comparison_status"] == "BLOCKED_REFERENCE"
    reference = report["reference"]
    assert reference["input_metadata"]["declared_pixels"] == 8192 * 6144
    assert reference["admission"]["error"]["code"] == "INVALID_IMAGE"
    assert report["totals"]["n"] == report["totals"]["not_evaluated"] == len(fixed_files) - 1
    assert report["totals"]["unverifiable"] == report["totals"]["false_accepts"] == 0
    for result in report["results"]:
        assert result["outcome"] == "NOT_EVALUATED" and result["score"] is None
        assert result["standalone_assessment"]["check"] == "SEAL_WHOLE_FRAME_REFERENCE_ASSESSMENT"
        assert "quality_gate" in result["standalone_assessment"]

    with pytest.raises(ApiError) as caught:
        seal_method((manifest.parent / reference["file"]).read_bytes(), {"min_inliers": 0})
    assert caught.value.code == "SEAL_PROFILE_INVALID"


def _moved_tape() -> tuple[bytes, bytes]:
    rng = np.random.default_rng(19)
    box = np.full((480, 640), 120, np.uint8)
    cv2.line(box, (0, 240), (640, 240), 65, 2)
    for _ in range(230):
        x, y = rng.integers(10, 630), rng.integers(10, 470)
        cv2.rectangle(box, (x, y), (x + rng.integers(2, 9), y + rng.integers(2, 9)), int(rng.integers(45, 105)), 1)
    tape = np.full((130, 340), 220, np.uint8)
    for _ in range(550):
        x, y = rng.integers(0, 340), rng.integers(0, 130)
        cv2.circle(tape, (x, y), int(rng.integers(2, 5)), int(rng.integers(30, 145)), -1)
    cv2.putText(tape, "SEAL 4471", (44, 78), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 35, 3)
    baseline, moved = box.copy(), box.copy()
    baseline[175:305, 150:490] = tape
    moved[204:334, 177:517] = tape
    return _png(baseline), _png(moved)


@pytest.mark.parametrize("roi", [(135, 160, 395, 185), None])
def test_detects_tape_shift_relative_to_stationary_box(roi):
    reference, probe = _moved_tape()
    evidence = _compare(reference, probe, replace(PROFILE, roi=roi))
    assert evidence.result == "SUSPICIOUS"
    assert evidence.metrics["registration_basis"] == ("OUTSIDE_SEAM" if roi else "OUTER_FRAME")
    assert evidence.metrics["inliers"] >= PROFILE.min_inliers


@pytest.mark.parametrize("roi", [(135, 160, 395, 185), None])
def test_surrounding_box_supports_intact_reframed_capture(roi):
    reference, _ = _moved_tape()
    frame = cv2.imdecode(np.frombuffer(reference, np.uint8), cv2.IMREAD_GRAYSCALE)
    height, width = frame.shape
    pose = cv2.getRotationMatrix2D((width / 2, height / 2), 2, 1.02)
    pose[:, 2] += (20, 15)
    probe = cv2.warpAffine(frame, pose, (width, height), borderMode=cv2.BORDER_REPLICATE)

    evidence = _compare(reference, _png(probe), replace(PROFILE, roi=roi))
    assert evidence.result == "INTACT"
    assert evidence.metrics["surrounding_support"] is True
    assert evidence.metrics["center_shift_ratio"] > evidence.metrics["thresholds"]["max_unanchored_center_shift"]


@pytest.mark.parametrize("roi", [(100, 260, 760, 200), None])
def test_moved_tape_cannot_register_against_itself_on_plain_box(reference, roi):
    frame = cv2.imdecode(np.frombuffer(reference, np.uint8), cv2.IMREAD_COLOR)
    probe = frame.copy()
    probe[260:460, 100:860] = frame[60:260, 100:860]
    cv2.line(probe, (100, 360), (859, 360), (40, 70, 95), 3)
    probe[220:420, 125:885] = frame[260:460, 100:860]
    ok, encoded = cv2.imencode(".jpg", probe)
    assert ok

    evidence = _compare(reference, encoded.tobytes(), replace(PROFILE, roi=roi))
    _no_registration(evidence, "REGISTRATION_FAILED")
    assert evidence.metrics["inliers"] >= PROFILE.min_inliers
    assert evidence.metrics["surrounding_support"] is False
    assert evidence.metrics["center_shift_ratio"] > evidence.metrics["thresholds"]["max_unanchored_center_shift"]
    assert evidence.metrics["registration_failure"] == "UNANCHORED_FRAME_SHIFT"
    assert evidence.metrics["anchor_registration_failure"] == ("TOO_FEW_MATCHES" if roi else "OUTER_FRAME_INLIER_SPAN")


def test_unusable_reference_and_poor_light(reference):
    enrollment = service.assess_reference(reference, PROFILE, evidence_source=SOURCE)
    assert enrollment.usable and enrollment.evidence_source == SOURCE and enrollment.keypoints >= PROFILE.min_inliers
    dark = render_seal("bad_light", 3)
    rejected = service.assess_reference(dark, PROFILE, evidence_source=SOURCE)
    assert not rejected.usable and not rejected.quality_gate["passed"]
    assert "IMAGE_QUALITY_BLUR" in rejected.reason_codes
    _no_registration(_compare(reference, dark), "IMAGE_QUALITY_DARK")
    _no_registration(_compare(dark, reference), "IMAGE_QUALITY_DARK")


def test_blank_and_striped_probe_never_register(reference):
    blank = np.full((480, 640), 125, np.uint8)
    stripes = np.tile(np.repeat([50, 215], 5), (480, 64)).astype(np.uint8)
    _no_registration(_compare(reference, _png(blank)), "IMAGE_QUALITY_BLUR")
    _no_registration(_compare(reference, _png(stripes)), "TOO_FEW_KEYPOINTS")


def test_unrelated_texture_and_partial_seam_coverage(reference):
    texture = np.random.default_rng(17).integers(30, 220, (720, 960), np.uint8)
    _no_registration(_compare(reference, _png(texture)), "REGISTRATION_FAILED")
    cropped = np.array(Image.open(BytesIO(reference)))[:, 210:]
    evidence = _compare(reference, _png(cropped))
    _no_registration(evidence, "REGISTRATION_FAILED")
    assert evidence.metrics["inliers"] >= PROFILE.min_inliers
    assert evidence.metrics["seam_coverage"] < evidence.metrics["thresholds"]["min_seam_coverage"]


def test_degenerate_ransac_transform_is_unverifiable(reference, monkeypatch):
    def degenerate(source, target, *args):
        return np.diag([1.0, 0.001, 1.0]), np.ones((len(source), 1), np.uint8)

    monkeypatch.setattr(cv2, "findHomography", degenerate)
    evidence = _compare(reference, reference)
    _no_registration(evidence, "REGISTRATION_FAILED")
    assert evidence.metrics["anchor_good_matches"] >= PROFILE.min_inliers
    assert evidence.metrics["anchor_inliers"] is None
    assert evidence.metrics["anchor_registration_failure"] == evidence.metrics["registration_failure"] == "INVALID_HOMOGRAPHY"


def test_ambiguous_similarity_keeps_registered_evidence(reference):
    evidence = _compare(reference, render_seal("intact", 2), replace(PROFILE, intact_ssim=0.995, suspicious_ssim=0.80))
    assert evidence.result == "UNVERIFIABLE"
    assert evidence.explanation == "Ambiguous similarity, recapture."
    assert evidence.quality_gate["passed"]
    assert 0.80 <= evidence.metrics["ssim"] < 0.995
    assert evidence.aligned_png is not None and evidence.diff_png is not None


def test_exif_orientation_and_original_resolution_seam_roi(reference):
    original = Image.open(BytesIO(reference)).resize((1440, 1080)).convert("L")
    buffer = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    original.transpose(Image.Transpose.ROTATE_90).save(buffer, format="PNG", exif=exif)
    profile = replace(PROFILE, roi=(150, 390, 1140, 300))
    evidence = _compare(_png(np.array(original)), buffer.getvalue(), profile)
    assert evidence.result == "INTACT"
    assert evidence.quality_gate["width"] == 1440 and evidence.quality_gate["height"] == 1080
    assert Image.open(BytesIO(evidence.aligned_png)).size == (760, 200)
    _no_registration(_compare(reference, reference, replace(PROFILE, roi=(940, 680, 100, 100))), "REGISTRATION_FAILED")


def test_large_oriented_jpeg_uses_bounded_pixels_and_source_roi(reference):
    source = Image.open(BytesIO(reference)).convert("L").resize((6001, 4501))
    buffer = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    source.transpose(Image.Transpose.ROTATE_90).save(buffer, format="JPEG", quality=90, exif=exif)
    data = buffer.getvalue()
    decoded = service._decode(data, "reference")
    assert decoded.original_size == (6001, 4501)
    assert service.WORKING_SIDE <= max(decoded.gray.shape) <= service.MAX_DECODED_SIDE

    evidence = _compare(data, data, replace(PROFILE, roi=(750, 1500, 4501, 1501)))
    assert evidence.result == "INTACT"
    assert (evidence.quality_gate["width"], evidence.quality_gate["height"]) == (6001, 4501)
    assert Image.open(BytesIO(evidence.aligned_png)).size == (722, 242)


def test_original_phone_enrollment_decodes_50_megapixels(phone_capture):
    profile = replace(PROFILE, roi=(1536, 1950, 5800, 1350))
    decoded = service._decode(phone_capture, "reference")
    assert decoded.original_size == (8192, 6144)
    assert service.WORKING_SIDE <= max(decoded.gray.shape) <= service.MAX_DECODED_SIDE
    assert service.assess_reference(phone_capture, profile, evidence_source="PRERECORDED_EVAL").usable

    self_check = service.compare(phone_capture, phone_capture, profile, evidence_source="PRERECORDED_EVAL")
    assert self_check.result == "INTACT" and self_check.metrics["ssim"] == 1.0
    assert self_check.metrics["anchor_inliers"] == self_check.metrics["inliers"] >= profile.min_inliers
    assert self_check.metrics["anchor_registration_failure"] is self_check.metrics["registration_failure"] is None
    assert Image.open(BytesIO(self_check.aligned_png)).size == (680, 159)


def test_jpeg_trailer_preserves_pixels_but_thumbnail_eoi_cannot_replace_main_eoi(reference):
    trailer = reference + b"Vivo proprietary metadata\xff\xd9\0"
    assert _compare(reference, trailer) == _compare(reference, reference)

    thumbnail = BytesIO()
    Image.new("L", (24, 24), 128).save(thumbnail, format="JPEG")
    embedded = thumbnail.getvalue()
    app = b"\xff\xe2" + (len(embedded) + 2).to_bytes(2, "big") + embedded
    incomplete = reference[:2] + app + reference[2:-2] + b"Vivo metadata"
    with pytest.raises(errors.ApiError) as error:
        _compare(reference, incomplete)
    assert (error.value.status, error.value.code) == (422, "INVALID_IMAGE")


def test_jpeg_with_undecodable_pixels_raises_422(reference):
    quantization_table = reference.index(b"\xff\xdb") + 4
    malformed = reference[:quantization_table] + b"\xff" + reference[quantization_table + 1:]
    with pytest.raises(errors.ApiError, match="probe") as error:
        _compare(reference, malformed)
    assert (error.value.status, error.value.code) == (422, "INVALID_IMAGE")


@pytest.mark.parametrize("capture_fixture", ["reference", "phone_capture"])
def test_premature_eoi_cannot_trigger_partial_jpeg_recovery(reference, request, capture_fixture):
    capture = request.getfixturevalue(capture_fixture)
    incomplete = capture[:jpeg_frame(capture).end * 3 // 4] + b"\xff\xd9"
    assert jpeg_frame(incomplete).end == len(incomplete)

    adapters = FakePorts(reference)
    response = _client(adapters).post(ROUTE, files={"image": ("incomplete.jpg", incomplete, "image/jpeg")})
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_IMAGE"
    assert not adapters.recorded


@pytest.mark.parametrize("malformed", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_invalid_bytes_raise_422(reference, malformed):
    with pytest.raises(errors.ApiError, match="probe") as error:
        _compare(reference, malformed)
    assert (error.value.status, error.value.code) == (422, "INVALID_IMAGE")


def test_rejects_truncation_oversized_dimensions_and_alpha(reference):
    rgba = np.zeros((480, 640, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[120, 160, 3] = 0
    png = _png(np.full((480, 640), 125, np.uint8))
    oversized = _png(np.zeros((240, service.MAX_IMAGE_SIDE + 1), np.uint8))
    oversized_pixels = _png(np.zeros((5000, 5000), np.uint8))
    oversized_jpeg = BytesIO()
    Image.new("L", (8192, 7813), 120).save(oversized_jpeg, format="JPEG")
    oversized_bytes = reference.ljust(service.MAX_IMAGE_BYTES + 1, b"\0")
    for data in (reference[:-15], png[:-12], _png(rgba), oversized, oversized_pixels, oversized_jpeg.getvalue(), oversized_bytes):
        with pytest.raises(errors.ApiError) as error:
            _compare(reference, data)
        assert (error.value.status, error.value.code) == (422, "INVALID_IMAGE")


@pytest.mark.parametrize("changes", [{"blur_min": "80"}, {"min_inliers": 0}, {"suspicious_ssim": 0.95}, {"glare_max": float("nan")}])
def test_invalid_profile_fails_explicitly(reference, changes):
    with pytest.raises(errors.ApiError) as error:
        _compare(reference, reference, replace(PROFILE, **changes))
    assert (error.value.status, error.value.code) == (422, "SEAL_PROFILE_INVALID")


class FakePorts:
    def __init__(self, reference: bytes):
        self.context = SealContext("pkg_demo", "rev1", reference, PROFILE)
        self.loaded = []
        self.recorded = []

    def load_seal_context(self, db, package_id, principal):
        self.loaded.append((db, package_id, principal))
        return self.context

    def record_recheck(self, db, ctx, evidence, probe, media_type, evidence_source, principal):
        self.recorded.append((ctx, evidence, probe, media_type, evidence_source, principal))
        return {"package_id": ctx.package_id, "revision_id": ctx.revision_id, "result": evidence.result, "evidence_source": evidence_source}


def _client(adapters, role="dispatcher") -> TestClient:
    app = FastAPI()
    errors.install(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[ports] = lambda: adapters
    app.dependency_overrides[current_principal] = lambda: Principal("u1", "tester", "Tester", (role,))
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("physical", [False, True])
def test_route_records_original_bytes_source_and_revision(reference, phone_capture, physical):
    source = "PRERECORDED_EVAL" if physical else SOURCE
    if physical:
        reference = probe = phone_capture
    else:
        probe = render_seal("intact", 2) + b"Vivo proprietary metadata"
    adapters = FakePorts(reference)
    response = _client(adapters).post(ROUTE, data={"evidence_source": source}, files={"image": ("seal.jpg", probe, "image/jpeg")})
    assert response.status_code == 200, response.text
    assert response.json() == {"package_id": "pkg_demo", "revision_id": "rev1", "result": "INTACT", "evidence_source": source}
    [(ctx, evidence, saved, media, saved_source, principal)] = adapters.recorded
    assert ctx is adapters.context and evidence.evidence_source == saved_source == source
    assert saved == probe and media == "image/jpeg" and principal.id == "u1"


def test_route_reports_unavailable_jpeg_validation(reference, monkeypatch):
    monkeypatch.setitem(sys.modules, "simplejpeg", None)
    adapters = FakePorts(reference)
    response = _client(adapters).post(ROUTE, data={"evidence_source": SOURCE}, files={"image": ("seal.jpg", reference, "image/jpeg")})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "JPEG_DECODER_UNAVAILABLE"
    assert "simplejpeg==1.9.0" in error["details"]["remediation"]
    assert not adapters.recorded


def test_route_rejects_forbidden_invalid_source_wrong_media_and_oversized_upload(reference):
    adapters = FakePorts(reference)
    files = {"image": ("seal.jpg", reference, "image/jpeg")}
    forbidden = _client(adapters, "operator").post(ROUTE, data={"evidence_source": SOURCE}, files=files)
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "ROLE_FORBIDDEN"
    client = _client(adapters)
    invalid_source = client.post(ROUTE, data={"evidence_source": "UPLOAD"}, files=files)
    assert invalid_source.status_code == 422 and invalid_source.json()["error"]["code"] == "EVIDENCE_SOURCE_INVALID"
    mismatch = client.post(ROUTE, data={"evidence_source": SOURCE}, files={"image": ("seal.png", reference, "image/png")})
    assert mismatch.status_code == 422 and mismatch.json()["error"]["code"] == "INVALID_IMAGE"
    oversized = client.post(ROUTE, data={"evidence_source": SOURCE}, files={"image": ("large.jpg", b"x" * (service.MAX_IMAGE_BYTES + 1), "image/jpeg")})
    assert oversized.status_code == 422 and oversized.json()["error"]["code"] == "INVALID_IMAGE"
    assert not adapters.loaded and not adapters.recorded
