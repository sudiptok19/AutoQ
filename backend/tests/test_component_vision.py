"""Component evidence, profile routing, and physical evaluation reporting."""

import json
import struct
import sys
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from zlib import crc32

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
from app.features.component_vision import service as vision
from app.features.component_vision._jpeg import jpeg_frame
from app.features.component_vision.router import router
from app.features.component_vision.service import (
    MAX_DECODED_SIDE,
    MAX_IMAGE_BYTES,
    METHOD_VERSION,
    evaluate,
    quality_gate,
    validate_reference,
)
from app.ports import VisionProfile, ports
from scripts.evaluate_vision import ROOT, evaluate_set, inspection_method

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


def test_physical_inspection_report_evaluates_original_captures(tmp_path):
    source = ROOT / "fixtures" / "images" / "inspection" / "manifest.json"
    images = json.loads(source.read_text(encoding="utf-8"))["images"]
    report = evaluate_set("inspection", source, tmp_path / "report.json")

    assert report["comparison_status"] == "COMPLETED"
    reference = report["reference"]
    assert reference["admission"]["passed"] is True
    assert reference["self_check"]["score"] == 0
    assert reference["self_check"]["artifacts"] == {"aligned_png": True, "diff_png": True}
    assert report["totals"]["n"] == len(images) - 1
    assert report["totals"]["not_evaluated"] == 0
    for row in [reference, *report["results"]]:
        original = (source.parent / row["file"]).read_bytes()
        assert row["sha256"] == sha256(original).hexdigest()
        assert row["input_metadata"]["jpeg_trailing_bytes"] > 0
        assert row["evidence_source"] == "PRERECORDED_EVAL"

    results = {row["file"]: row for row in report["results"]}
    good = results["good_02.jpg"]
    defect = results["defect_02.jpg"]
    assert good["outcome"] == "ACCEPTED" and defect["outcome"] == "FLAGGED"
    assert good["score"] < defect["score"]
    assert good["metrics"]["alignment"]["failure"] is None

    off_center = results["good_01.jpg"]
    assert off_center["outcome"] == "UNVERIFIABLE"
    assert off_center["reasons"] == ["ALIGNMENT_FAILED"] and off_center["score"] is None
    check_alignment_diagnostic(off_center["metrics"]["alignment"])
    assert off_center["artifacts"] == {"aligned_png": False, "diff_png": False}
    for name, reason in {"blurry_01.jpg": "IMAGE_QUALITY_BLUR", "misaligned_01.jpg": "ALIGNMENT_FAILED"}.items():
        row = results[name]
        assert row["outcome"] == "UNVERIFIABLE" and row["score"] is None
        assert reason in row["reasons"]
        assert row["artifacts"] == {"aligned_png": False, "diff_png": False}

    with pytest.raises(ApiError, match="Invalid inspection threshold min_inliers"):
        inspection_method((source.parent / reference["file"]).read_bytes(), {"min_inliers": 0})


def test_primary_jpeg_can_contain_a_thumbnail_and_trailing_metadata(part, profile):
    original = (ROOT / "fixtures" / "images" / "inspection" / "golden_01.jpg").read_bytes()
    frame = jpeg_frame(original)
    primary = original[:frame.end]
    assert original[frame.end:] and not original.endswith(b"\xff\xd9")
    assert quality_gate(original, profile.thresholds) == quality_gate(primary, profile.thresholds)

    thumbnail = encode(cv2.resize(part, (32, 24)), ".jpg")
    app_segment = b"\xff\xe3" + struct.pack(">H", len(thumbnail) + 2) + thumbnail
    nested = primary[:2] + app_segment + primary[2:]
    jpeg = nested + b"vendor trailer with marker bytes \xff\xd9"
    assert jpeg_frame(jpeg).end == len(nested)
    assert quality_gate(jpeg, profile.thresholds) == quality_gate(primary, profile.thresholds)
    with pytest.raises(ApiError) as caught:
        quality_gate(nested[:-2], profile.thresholds)
    assert (caught.value.status, caught.value.code) == (422, "INVALID_IMAGE")


@pytest.mark.parametrize("percent_of_file", [50, 95])
def test_strict_decoder_rejects_premature_eoi_in_real_entropy_stream(percent_of_file):
    original = (ROOT / "fixtures" / "images" / "inspection" / "golden_01.jpg").read_bytes()
    frame = jpeg_frame(original)
    shortened = original[:frame.end * percent_of_file // 100].rstrip(b"\xff")
    corrupt = shortened + b"\xff\xd9" + original[frame.end:]
    assert jpeg_frame(corrupt).end == len(shortened) + 2
    with pytest.raises(ApiError, match="JPEG scan could not be decoded completely") as caught:
        quality_gate(corrupt, {})
    assert (caught.value.status, caught.value.code) == (422, "INVALID_IMAGE")


def test_missing_strict_decoder_is_explicit_and_only_affects_jpeg(part, golden, profile, monkeypatch):
    jpeg = encode(part, ".jpg")
    jpeg_profile = replace(profile, reference_sha256=sha256(jpeg).hexdigest())
    monkeypatch.setitem(sys.modules, "simplejpeg", None)
    assert quality_gate(golden, {})["passed"]
    with pytest.raises(ApiError) as capture_error:
        quality_gate(jpeg, {})
    with pytest.raises(ApiError) as reference_error:
        evaluate(golden, jpeg, jpeg_profile, evidence_source=SOURCE)
    for error in (capture_error.value, reference_error.value):
        assert (error.status, error.code) == (503, "JPEG_DECODER_UNAVAILABLE")
        assert "simplejpeg" in error.details["remediation"]


def test_cmyk_jpeg_remains_supported(part):
    data = BytesIO()
    Image.fromarray(cv2.cvtColor(part, cv2.COLOR_BGR2RGB)).convert("CMYK").save(data, format="JPEG")
    gate = quality_gate(data.getvalue(), {})
    assert gate["passed"]
    assert (gate["width"], gate["height"]) == (640, 480)


def test_excessive_translation_gives_alignment_failure(part, golden, profile):
    translated = cv2.warpAffine(part, np.float32([[1, 0, 175], [0, 1, 0]]), (640, 480))
    evidence = evaluate(encode(translated), golden, profile, evidence_source=SOURCE)
    check_unavailable(evidence, "ALIGNMENT_FAILED")
    assert evidence.quality_gate["passed"] is True
    assert evidence.analysis["calibrated"] is True
    check_alignment_diagnostic(evidence.analysis["alignment"])


def check_alignment_diagnostic(alignment):
    assert alignment["failure"] in {
        "NO_DESCRIPTORS", "TOO_FEW_MATCHES", "HOMOGRAPHY_FAILED", "TOO_FEW_INLIERS",
        "LOW_INLIER_RATIO", "LOW_INLIER_COVERAGE", "INVALID_PROJECTED_FRAME",
        "LOW_FRAME_OVERLAP", "LOW_ROI_SUPPORT", "LOW_CORRELATION",
    }
    assert alignment["probe_keypoints"] >= 0
    assert alignment["reference_keypoints"] >= 0
    if "matches" in alignment:
        assert 0 <= alignment["matches"] <= min(alignment["probe_keypoints"], alignment["reference_keypoints"])
    if "inliers" in alignment:
        assert 0 <= alignment["inliers"] <= alignment["matches"]
        assert alignment["inlier_ratio"] == pytest.approx(alignment["inliers"] / alignment["matches"], abs=1e-6)


@pytest.mark.parametrize(("offset", "failure"), [(160, "LOW_FRAME_OVERLAP"), (0, "LOW_ROI_SUPPORT")])
def test_alignment_reports_the_first_failed_geometry_check(golden, profile, monkeypatch, offset, failure):
    # Supply fixed correspondences and a fitted model so ORB and RANSAC cannot change the first failing check.
    points = [(32, 32), (224, 32), (416, 32), (32, 224), (416, 224), (32, 416), (224, 416), (416, 416)]
    probe = [cv2.KeyPoint(x, y, 1) for x, y in points]
    reference = [cv2.KeyPoint(x + offset, y, 1) for x, y in points]
    descriptors = np.zeros((len(points), 32), dtype=np.uint8)
    features = iter(((probe, descriptors), (reference, descriptors)))
    model = np.array([[1, 0, offset], [0, 1, 0], [0, 0, 1]], dtype=np.float64)
    monkeypatch.setattr(vision, "_features", lambda gray: next(features))
    monkeypatch.setattr(vision, "_matches", lambda first, second: [(i, i) for i in range(len(points))])
    monkeypatch.setattr(cv2, "findHomography", lambda *args, **kwargs: (model, np.ones((len(points), 1), dtype=np.uint8)))
    tiny_roi = replace(profile, thresholds={**profile.thresholds, "min_inliers": 8, "roi": (0.4, 0.4, 0.01, 0.01)})

    evidence = evaluate(golden, golden, tiny_roi, evidence_source=SOURCE)

    check_unavailable(evidence, "ALIGNMENT_FAILED")
    alignment = evidence.analysis["alignment"]
    assert alignment["failure"] == failure
    assert alignment["inliers"] == len(points)
    assert alignment["inlier_ratio"] == 1.0
    overlap = min(alignment["projected_probe_overlap"], alignment["reference_overlap"])
    if offset:
        assert overlap < alignment["thresholds"]["min_overlap"]
        assert "usable_roi_pixels" not in alignment
    else:
        assert overlap >= alignment["thresholds"]["min_overlap"]
        assert alignment["usable_roi_pixels"] < alignment["thresholds"]["min_usable_roi_pixels"]
    assert "correlation" not in alignment


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
    alignment = result.analysis["alignment"]
    assert alignment["failure"] is None
    assert alignment["usable_roi_pixels"] >= alignment["thresholds"]["min_usable_roi_pixels"]
    assert alignment["correlation"] >= alignment["thresholds"]["min_correlation"]
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


@pytest.mark.parametrize("condition", [
    "garbage", "missing_jpeg_eoi", "early_jpeg_eoi", "invalid_jpeg_segment",
    "truncated_png", "oversize_bytes", "oversize_edge",
])
def test_invalid_and_oversize_image_bytes_are_typed_errors(part, golden, profile, condition):
    if condition == "garbage":
        image = b"not a JPEG or PNG"
    elif condition == "missing_jpeg_eoi":
        image = encode(part, ".jpg", (cv2.IMWRITE_JPEG_PROGRESSIVE, 1))
        assert validate_reference(image, profile.thresholds) == "image/jpeg"
        assert image.endswith(b"\xff\xd9")
        image = image[:-2]
    elif condition == "early_jpeg_eoi":
        image = encode(part, ".jpg")
        scan = image.index(b"\xff\xda") + 2
        end_of_header = scan + int.from_bytes(image[scan:scan + 2], "big")
        image = image[:end_of_header + 3] + b"\xff\xd9"
    elif condition == "invalid_jpeg_segment":
        image = encode(part, ".jpg")
        image = image[:2] + b"\xff\xe3\xff\xff" + image[2:]
    elif condition == "truncated_png":
        image = golden[:-12]
    elif condition == "oversize_bytes":
        image = b"\x89PNG\r\n\x1a\n".ljust(MAX_IMAGE_BYTES + 1)
    else:
        image = encode(np.zeros((5, 8193), dtype=np.uint8))
    with pytest.raises(ApiError) as caught:
        evaluate(image, golden, profile, evidence_source=SOURCE)
    assert caught.value.status == 422
    assert caught.value.code == "INVALID_IMAGE"
    assert caught.value.details["component"] == "component_vision"
    assert caught.value.details["remediation"]


def test_ordinary_jpeg_quality_uses_legacy_capture_pixels(part):
    image = encode(cv2.resize(part, (2600, 1950), interpolation=cv2.INTER_LINEAR), ".jpg")
    decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
    measured = cv2.resize(gray, (1280, 960), interpolation=cv2.INTER_AREA)
    expected_blur = float(cv2.meanStdDev(cv2.Laplacian(measured, cv2.CV_32F, ksize=1))[1][0, 0] ** 2)
    expected_glare = cv2.countNonZero(cv2.inRange(gray, 250, 255)) / gray.size
    gate = quality_gate(image, {})
    assert (gate["width"], gate["height"]) == (2600, 1950)
    assert gate["blur_laplacian_var"] == expected_blur
    assert gate["glare_ratio"] == expected_glare


def test_low_resolution_jpeg_stops_before_alignment(part, profile):
    small = encode(cv2.resize(part, (256, 192), interpolation=cv2.INTER_AREA), ".jpg")
    evidence = evaluate(small, b"unused reference", profile, evidence_source=SOURCE)
    check_unavailable(evidence, "IMAGE_QUALITY_RESOLUTION")
    assert (evidence.quality_gate["width"], evidence.quality_gate["height"]) == (256, 192)


def test_large_jpeg_is_decoded_at_bounded_scale_and_uses_oriented_capture_dimensions(monkeypatch):
    height, width = 7999, 8001
    image = encode(np.full((height, width), 255, dtype=np.uint8), ".jpg")
    exif = Image.Exif()
    exif[274] = 6
    metadata = exif.tobytes()
    image = image[:2] + b"\xff\xe1" + struct.pack(">H", len(metadata) + 2) + metadata + image[2:]
    imdecode = cv2.imdecode

    def bounded_decode(buffer, flags):
        assert flags == cv2.IMREAD_REDUCED_COLOR_4 | cv2.IMREAD_IGNORE_ORIENTATION
        pixels = imdecode(buffer, flags)
        assert max(pixels.shape[:2]) <= MAX_DECODED_SIDE
        return pixels

    monkeypatch.setattr(cv2, "imdecode", bounded_decode)
    gate = quality_gate(image, {"min_w": 7000, "min_h": 8000})
    assert (gate["width"], gate["height"]) == (height, width)
    assert gate["glare_ratio"] == 1.0
    assert gate["failures"] == ["IMAGE_QUALITY_BLUR", "IMAGE_QUALITY_GLARE"]


def test_large_opaque_png_and_jpeg_over_pixel_caps_do_not_reach_decoder(monkeypatch):
    jpeg = encode(np.zeros((8000, 8001), dtype=np.uint8), ".jpg")
    png = encode(np.zeros((4800, 5001), dtype=np.uint8))

    def reject_decode(*_):
        pytest.fail("Oversized capture reached the native decoder")

    monkeypatch.setattr(cv2, "imdecode", reject_decode)
    for image in (jpeg, png):
        with pytest.raises(ApiError, match="dimensions exceed the supported limits") as caught:
            quality_gate(image, {})
        assert (caught.value.status, caught.value.code) == (422, "INVALID_IMAGE")


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
    jpeg = encode(part, ".jpg") + b"vendor trailer"
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
