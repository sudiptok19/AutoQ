"""Component evidence and profile routing with real SYNTHETIC_TEST JPEG/PNG bytes."""

import struct
from dataclasses import replace
from hashlib import sha256
from zlib import crc32

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import errors
from app.core.auth import Principal, current_principal
from app.core.errors import ApiError
from app.db.session import get_db
from app.features.component_vision.router import router
from app.features.component_vision.service import (
    MAX_IMAGE_BYTES,
    METHOD_VERSION,
    evaluate,
    quality_gate,
    validate_reference,
)
from app.ports import VisionProfile, ports

pytestmark = pytest.mark.nodb

SOURCE = "SYNTHETIC_TEST"
API = "/api/v1/inspection-profiles"


def encode(pixels, extension=".png", params=()):
    ok, data = cv2.imencode(extension, pixels, params)
    assert ok
    return data.tobytes()


@pytest.fixture(scope="module")
def part():
    img = np.full((480, 640, 3), (54, 62, 66), dtype=np.uint8)
    cv2.rectangle(img, (42, 45), (598, 435), (151, 164, 173), -1)
    rng = np.random.default_rng(732)
    for x in range(70, 575, 40):
        for y in range(70, 420, 40):
            center = (x + int(rng.integers(-10, 11)), y + int(rng.integers(-10, 11)))
            color = tuple(int(value) for value in rng.integers(25, 82, 3))
            cv2.circle(img, center, int(rng.integers(4, 13)), color, -1)
            cv2.line(img, center, (center[0] + 16, center[1] + 9), color, 2)
    return img


@pytest.fixture(scope="module")
def golden(part):
    return encode(part)


@pytest.fixture(scope="module")
def profile(golden):
    return VisionProfile(
        id="prof_test", part_id="part_test", station_id="station_test", method_version=METHOD_VERSION,
        calibration_version=3, calibrated=True, reference_sha256=sha256(golden).hexdigest(),
        thresholds={"hold": 0.45, "reject": 0.70},
    )


def check_unavailable(evidence, code):
    assert evidence.reason_codes == [code]
    assert evidence.evidence_source == SOURCE
    assert evidence.analysis["score"] is None
    assert evidence.analysis["regions"] == []
    assert evidence.raw_score is None
    assert evidence.aligned_png is None
    assert evidence.diff_png is None


@pytest.mark.parametrize(
    ("condition", "code"),
    [
        ("blur", "IMAGE_QUALITY_BLUR"),
        ("glare", "IMAGE_QUALITY_GLARE"),
        ("resolution", "IMAGE_QUALITY_RESOLUTION"),
    ],
)
def test_quality_gate_stops_evaluation(part, profile, condition, code):
    capture = part.copy()
    if condition == "blur":
        capture = cv2.GaussianBlur(capture, (39, 39), 13)
    elif condition == "glare":
        capture[:100, :] = 255
    else:
        capture = cv2.resize(capture, (256, 192), interpolation=cv2.INTER_AREA)
    image = encode(capture)
    # A bad reference must not hide the capture quality reason.
    evidence = evaluate(image, b"missing reference", profile, evidence_source=SOURCE)
    check_unavailable(evidence, code)
    assert evidence.quality_gate == quality_gate(image, profile.thresholds)
    assert evidence.quality_gate["passed"] is False
    assert (evidence.quality_gate["width"], evidence.quality_gate["height"]) == (
        capture.shape[1], capture.shape[0],
    )


@pytest.mark.parametrize(
    "condition", ["no_profile", "uncalibrated", "old_method", "no_reference", "wrong_digest", "invalid_reference"],
)
def test_missing_or_unusable_calibration(golden, profile, condition):
    reference = golden
    if condition == "no_profile":
        profile = None
    elif condition == "uncalibrated":
        profile = replace(profile, calibrated=False)
    elif condition == "old_method":
        profile = replace(profile, method_version="interim-gd-0.1")
    elif condition == "no_reference":
        reference = None
    elif condition == "wrong_digest":
        profile = replace(profile, reference_sha256="0" * 64)
    else:
        reference = b"unreadable golden image"
        profile = replace(profile, reference_sha256=sha256(reference).hexdigest())
    evidence = evaluate(golden, reference, profile, evidence_source=SOURCE)
    check_unavailable(evidence, "MODEL_UNCALIBRATED")
    assert evidence.quality_gate["passed"] is True
    assert evidence.analysis["calibrated"] is False
    assert evidence.analysis["method_version"] == METHOD_VERSION


def test_excessive_translation_gives_alignment_failure(part, golden, profile):
    translated = cv2.warpAffine(part, np.float32([[1, 0, 175], [0, 1, 0]]), (640, 480))
    evidence = evaluate(encode(translated), golden, profile, evidence_source=SOURCE)
    check_unavailable(evidence, "ALIGNMENT_FAILED")
    assert evidence.quality_gate["passed"] is True
    assert evidence.analysis["calibrated"] is True


def test_registered_difference_scores_visible_defect_and_emits_pngs(part, golden, profile):
    transformed = cv2.getRotationMatrix2D((320, 240), 2, 1.02)
    transformed[:, 2] += (9, -8)
    good = cv2.warpAffine(part, transformed, (640, 480), borderMode=cv2.BORDER_REPLICATE)
    defective = part.copy()
    cv2.rectangle(defective, (440, 198), (503, 262), (15, 24, 35), -1)
    damaged = cv2.warpAffine(defective, transformed, (640, 480), borderMode=cv2.BORDER_REPLICATE)
    good_evidence = evaluate(encode(good), golden, profile, evidence_source=SOURCE)
    damaged_bytes = encode(damaged)
    result = evaluate(damaged_bytes, golden, profile, evidence_source=SOURCE)

    assert 0 <= good_evidence.raw_score < profile.thresholds["hold"]
    assert good_evidence.reason_codes == []
    assert profile.thresholds["reject"] <= result.raw_score <= 1
    assert result.reason_codes == ["SCORE_ABOVE_REJECT"]
    assert result.analysis["score"] == result.raw_score
    assert result.analysis["calibrated"] is True
    assert result.evidence_source == SOURCE
    region = result.analysis["regions"][0]
    assert region["x"] <= 475 < region["x"] + region["w"]
    assert region["y"] <= 230 < region["y"] + region["h"]
    for png in (result.aligned_png, result.diff_png):
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).shape == part.shape
    assert result.aligned_png != result.diff_png
    assert result == evaluate(damaged_bytes, golden, profile, evidence_source=SOURCE)

    scoped_profile = replace(profile, thresholds={**profile.thresholds, "roi": (0.1, 0.1, 0.5, 0.8)})
    scoped = evaluate(damaged_bytes, golden, scoped_profile, evidence_source=SOURCE)
    assert 0 <= scoped.raw_score < profile.thresholds["hold"]
    assert scoped.reason_codes == []


def test_records_applied_thresholds_and_reports_hold_evidence(part, golden, profile):
    changed = part.copy()
    cv2.rectangle(changed, (465, 225), (496, 257), (75, 85, 90), -1)
    image = encode(changed)
    measured = evaluate(image, golden, profile, evidence_source=SOURCE)
    assert 0 < measured.raw_score < 1
    selected = replace(profile, thresholds={
        "hold": measured.raw_score, "reject": (measured.raw_score + 1.0) / 2, "tightened": True,
    })
    result = evaluate(image, golden, selected, evidence_source=SOURCE)
    assert result.raw_score == measured.raw_score
    assert result.reason_codes == ["SCORE_ABOVE_HOLD"]
    assert result.analysis["hold_threshold"] == selected.thresholds["hold"]
    assert result.analysis["reject_threshold"] == selected.thresholds["reject"]
    assert result.analysis["threshold_tightened"] is True


@pytest.mark.parametrize("condition", ["garbage", "missing_jpeg_eoi", "truncated_png", "oversize_bytes", "oversize_edge"])
def test_invalid_and_oversize_image_bytes_are_typed_errors(part, golden, profile, condition):
    if condition == "garbage":
        image = b"not a JPEG or PNG"
    elif condition == "missing_jpeg_eoi":
        image = encode(part, ".jpg", (cv2.IMWRITE_JPEG_PROGRESSIVE, 1))
        assert validate_reference(image, profile.thresholds) == "image/jpeg"
        assert image.endswith(b"\xff\xd9")
        image = image[:-2]
    elif condition == "truncated_png":
        image = golden[:-12]
    elif condition == "oversize_bytes":
        image = b"\x89PNG\r\n\x1a\n".ljust(MAX_IMAGE_BYTES + 1)
    else:
        image = encode(np.zeros((5, 8193), dtype=np.uint8))
    with pytest.raises(ApiError) as caught:
        evaluate(image, golden, profile, evidence_source=SOURCE)
    assert caught.value.status == (413 if condition.startswith("oversize") else 422)
    assert caught.value.code == "INVALID_IMAGE"
    assert caught.value.details["component"] == "component_vision"
    assert caught.value.details["remediation"]


def test_png_transparency_cannot_supply_hidden_inspection_pixels(part, golden, profile):
    alpha_image = cv2.cvtColor(part, cv2.COLOR_BGR2BGRA)
    opaque = encode(alpha_image)
    assert quality_gate(opaque, profile.thresholds) == quality_gate(golden, profile.thresholds)
    assert validate_reference(opaque, profile.thresholds, "image/png") == "image/png"

    alpha_image[140:320, 150:350, 3] = 0
    gray_pixels = cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
    gray = encode(gray_pixels)
    # Make a real grayscale PNG whose color key marks the background as transparent.
    chunk = b"tRNS" + struct.pack(">H", int(gray_pixels[0, 0]))
    keyed_gray = gray[:33] + struct.pack(">I", 2) + chunk + struct.pack(">I", crc32(chunk)) + gray[33:]
    for image in (encode(alpha_image), keyed_gray):
        with pytest.raises(ApiError) as caught:
            evaluate(image, golden, profile, evidence_source=SOURCE)
        assert (caught.value.status, caught.value.code) == (422, "INVALID_IMAGE")
        assert "opaque" in caught.value.message


def test_invalid_source_and_profile_are_typed_errors(golden, profile):
    with pytest.raises(ApiError) as source_error:
        evaluate(golden, golden, profile, evidence_source="UPLOAD")
    assert (source_error.value.status, source_error.value.code) == (422, "EVIDENCE_SOURCE_INVALID")
    assert source_error.value.details["remediation"]
    invalid_roi = replace(profile, thresholds={**profile.thresholds, "roi": (0.1, 0.1, 1, 1)})
    with pytest.raises(ApiError) as profile_error:
        evaluate(golden, golden, invalid_roi, evidence_source=SOURCE)
    assert (profile_error.value.status, profile_error.value.code) == (422, "VISION_PROFILE_INVALID")
    assert profile_error.value.details["remediation"]


def test_artifact_encoding_failure_does_not_return_a_score(golden, profile, monkeypatch):
    monkeypatch.setattr(cv2, "imencode", lambda *args: (False, None))
    with pytest.raises(ApiError) as caught:
        evaluate(golden, golden, profile, evidence_source=SOURCE)
    assert (caught.value.status, caught.value.code) == (503, "VISION_EVIDENCE_FAILED")
    assert caught.value.details["remediation"]


class FakeProfilePorts:
    def __init__(self, profile):
        self.profile = profile
        self.saved = []

    def list_profiles(self, db, principal):
        return [self.profile]

    def load_profile(self, db, profile_id, principal):
        if profile_id != self.profile.id:
            raise ApiError(404, "PROFILE_NOT_FOUND", "Inspection profile not found")
        return self.profile

    def save_reference(self, db, profile_id, image, media_type, principal):
        self.saved.append((profile_id, image, media_type, principal))
        return replace(
            self.profile, id="prof_next", calibrated=False,
            calibration_version=self.profile.calibration_version + 1, reference_sha256=sha256(image).hexdigest(),
        )


def profile_client(fake, principal=None):
    app = FastAPI()
    errors.install(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[ports] = lambda: fake
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def test_profile_list_requires_authentication_and_allows_operator(profile):
    fake = FakeProfilePorts(profile)
    with profile_client(fake) as unauthenticated:
        response = unauthenticated.get(API)
    assert (response.status_code, response.json()["error"]["code"]) == (401, "UNAUTHENTICATED")
    operator = Principal("op_1", "operator", "Operator", ("operator",))
    with profile_client(fake, operator) as client:
        response = client.get(API)
    assert response.status_code == 200
    assert response.json() == {
        "items": [{
            "id": profile.id, "part_id": profile.part_id, "station_id": profile.station_id,
            "method_version": METHOD_VERSION, "calibrated": True,
            "calibration_version": profile.calibration_version, "reference_sha256": profile.reference_sha256,
        }],
        "next_cursor": None,
    }


@pytest.mark.parametrize("role", ["quality_engineer", "quality_manager"])
def test_golden_upload_passes_validated_bytes_to_ports_and_creates_uncalibrated_revision(part, profile, role):
    fake = FakeProfilePorts(profile)
    principal = Principal("quality_1", "quality", "Quality", (role,))
    jpeg = encode(part, ".jpg")
    with profile_client(fake, principal) as client:
        response = client.post(f"{API}/{profile.id}/golden", files={"image": ("golden.jpg", jpeg, "image/jpeg")})
    assert response.status_code == 200, response.json()
    saved = response.json()
    assert saved["id"] == "prof_next" and saved["calibrated"] is False
    assert saved["calibration_version"] == profile.calibration_version + 1
    assert saved["reference_sha256"] == sha256(jpeg).hexdigest()
    assert fake.saved == [(profile.id, jpeg, "image/jpeg", principal)]


def test_invalid_or_forbidden_golden_upload_never_reaches_save_reference(part, golden, profile):
    fake = FakeProfilePorts(profile)
    operator = Principal("op_1", "operator", "Operator", ("operator",))
    engineer = Principal("qe_1", "engineer", "Quality Engineer", ("quality_engineer",))
    valid_file = {"image": ("golden.png", golden, "image/png")}
    path = f"{API}/{profile.id}/golden"
    with profile_client(fake, operator) as client:
        denied = client.post(path, files=valid_file)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "ROLE_FORBIDDEN")
    with profile_client(fake, engineer) as client:
        unknown = client.post(f"{API}/missing/golden", files=valid_file)
        mismatched = client.post(path, files={"image": ("golden.jpg", golden, "image/jpeg")})
        blurry = client.post(path, files={"image": ("flat.png", encode(np.full_like(part, 128)), "image/png")})
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "PROFILE_NOT_FOUND")
    assert (mismatched.status_code, mismatched.json()["error"]["code"]) == (422, "INVALID_IMAGE")
    assert (blurry.status_code, blurry.json()["error"]["code"]) == (422, "INVALID_IMAGE")
    assert blurry.json()["error"]["details"]["reason_codes"] == ["IMAGE_QUALITY_BLUR"]
    assert fake.saved == []
