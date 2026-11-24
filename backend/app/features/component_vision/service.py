"""Deterministic golden-reference inspection evidence for one calibrated profile."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite, sqrt
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.errors import ApiError
from app.core.storage import sniff
from app.features.component_vision._jpeg import InvalidJpeg, JpegDecoderUnavailable, decode_jpeg, jpeg_frame
from app.ports import VisionProfile

METHOD_VERSION = "gd-0.3"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
MAX_PNG_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 8192
MAX_DECODED_SIDE = 2048
_MAX_FULL_DECODE_PIXELS = 24_000_000
_WORK_EDGE = 1280
_MEDIA_TYPES = {"image/jpeg", "image/png"}
_SOURCES = {"LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST"}

Image = NDArray[np.uint8]


@dataclass(frozen=True)
class VisionEvidence:
    quality_gate: dict
    analysis: dict
    reason_codes: list[str]
    aligned_png: bytes | None
    diff_png: bytes | None
    raw_score: float | None
    evidence_source: str | None = None


class InspectionDetector(Protocol):
    """The typed evidence interface used by the inspection policy."""

    def __call__(
        self, image: bytes, reference: bytes | None, profile: VisionProfile | None, *, evidence_source: str | None = None
    ) -> VisionEvidence: ...


@dataclass(frozen=True)
class _Registration:
    min_inliers: int
    min_inlier_ratio: float
    min_overlap: float
    min_coverage: float
    roi: tuple[float, float, float, float]


def _image_error(message: str, *, reasons: list[str] | None = None) -> ApiError:
    details = {"component": "component_vision", "remediation": "Capture a new JPEG or PNG within the size limit."}
    if reasons:
        details["reason_codes"] = reasons
    return ApiError(422, "INVALID_IMAGE", message, details)


def _profile_error(message: str) -> ApiError:
    return ApiError(422, "VISION_PROFILE_INVALID", message, {
        "component": "component_vision", "remediation": "Check the inspection profile configuration.",
    })


def _artifact_error(message: str) -> ApiError:
    return ApiError(503, "VISION_EVIDENCE_FAILED", message, {
        "component": "component_vision", "remediation": "Retry the inspection or contact the service operator.",
    })


def _media_type(data: bytes) -> str:
    if not isinstance(data, bytes) or not data:
        raise _image_error("The image must contain JPEG or PNG bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise _image_error("Image exceeds 10 MiB")
    media_type = sniff(data)
    if media_type not in _MEDIA_TYPES:
        raise _image_error("Only JPEG and PNG images are supported")
    return media_type


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or data[8:16] != b"\0\0\0\x0dIHDR":
        raise _image_error("PNG is missing a valid image header")
    offset = 8
    while offset + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        offset += size + 12
        if offset > len(data) or kind in (b"acTL", b"fcTL", b"fdAT"):
            break
        if kind == b"tRNS":
            # OpenCV can discard this chunk, including the color key in grayscale PNGs.
            raise _image_error("Use an opaque PNG or JPEG without a transparency key")
        if kind == b"IEND" and size == 0 and offset == len(data):
            return struct.unpack_from(">II", data, 16)
    raise _image_error("PNG must be a complete, single-frame image")


def _check_dimensions(width: int, height: int, max_pixels: int) -> None:
    if min(width, height) < 1 or max(width, height) > MAX_IMAGE_SIDE or width * height > max_pixels:
        raise _image_error("Decoded image dimensions exceed the supported limits")


def _decode(data: bytes) -> tuple[Image, str, tuple[int, int]]:
    media_type = _media_type(data)
    if media_type == "image/jpeg":
        try:
            frame = jpeg_frame(data)
            _check_dimensions(frame.width, frame.height, MAX_IMAGE_PIXELS)
            # Preserve the calibrated pixel path for the original admission range.
            large = frame.width * frame.height > _MAX_FULL_DECODE_PIXELS
            pixels, dimensions = decode_jpeg(data, frame, max_edge=MAX_DECODED_SIDE if large else None)
        except JpegDecoderUnavailable as exc:
            raise ApiError(503, "JPEG_DECODER_UNAVAILABLE", "JPEG validation is unavailable", {
                "component": "component_vision",
                "remediation": "Install simplejpeg==1.9.0 in the backend environment and retry.",
            }) from exc
        except InvalidJpeg as exc:
            raise _image_error(str(exc)) from exc
        return pixels, media_type, dimensions

    width, height = _png_dimensions(data)
    _check_dimensions(width, height, MAX_PNG_PIXELS)
    has_alpha = data[25] in (4, 6)
    flags = cv2.IMREAD_UNCHANGED if has_alpha else cv2.IMREAD_COLOR
    try:
        pixels = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), flags)
        if pixels is None or pixels.shape[:2] not in ((height, width), (width, height)):
            raise _image_error("Image could not be decoded at its declared dimensions")
        if has_alpha:
            if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels[:, :, 3].min() != np.iinfo(pixels.dtype).max:
                raise _image_error("Image contains transparent pixels. Use an opaque PNG or JPEG")
            pixels = cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)
            if pixels.dtype == np.uint16:
                pixels = (pixels >> 8).astype(np.uint8)
    except cv2.error as exc:
        raise _image_error("Image could not be decoded") from exc
    return pixels, media_type, (pixels.shape[1], pixels.shape[0])


def _number(config: dict, key: str, default: float, lower: float, upper: float, *, integer: bool = False) -> float:
    value = config.get(key, default)
    expected_types = (int,) if integer else (int, float)
    if type(value) not in expected_types or not lower <= value <= upper or not isfinite(value):
        raise _profile_error(f"Invalid inspection threshold {key}")
    return value


def _quality_thresholds(config: dict) -> dict:
    if not isinstance(config, dict):
        raise _profile_error("Inspection thresholds must be an object")
    return {
        "blur_min": _number(config, "blur_min", 80.0, 0, 1_100_000),
        "glare_max": _number(config, "glare_max", 0.12, 0, 1),
        "min_w": _number(config, "min_w", 320, 1, MAX_IMAGE_SIDE, integer=True),
        "min_h": _number(config, "min_h", 240, 1, MAX_IMAGE_SIDE, integer=True),
    }


def _working(pixels: Image) -> Image:
    height, width = pixels.shape[:2]
    scale = _WORK_EDGE / max(height, width)
    if scale >= 1:
        return pixels
    return cv2.resize(pixels, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)


def _quality(pixels: Image, dimensions: tuple[int, int], thresholds: dict) -> dict:
    width, height = dimensions
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    # Resolution uses capture dimensions, and optical metrics use the admitted pixels.
    laplacian = cv2.Laplacian(_working(gray), cv2.CV_32F, ksize=1)
    sharpness = float(cv2.meanStdDev(laplacian)[1][0, 0] ** 2)
    glare = cv2.countNonZero(cv2.inRange(gray, 250, 255)) / gray.size
    failures = []
    if width < thresholds["min_w"] or height < thresholds["min_h"]:
        failures.append("IMAGE_QUALITY_RESOLUTION")
    if sharpness < thresholds["blur_min"]:
        failures.append("IMAGE_QUALITY_BLUR")
    if glare > thresholds["glare_max"]:
        failures.append("IMAGE_QUALITY_GLARE")
    return {
        "passed": not failures, "blur_laplacian_var": sharpness, "glare_ratio": glare,
        "width": width, "height": height, "thresholds": thresholds, "failures": failures,
    }


def quality_gate(image: bytes, thresholds: dict) -> dict:
    """Measure JPEG/PNG quality. Dimensions refer to the full oriented capture."""
    pixels, _, dimensions = _decode(image)
    return _quality(pixels, dimensions, _quality_thresholds(thresholds))


def _registration(config: dict) -> _Registration:
    roi = config.get("roi", (0, 0, 1, 1))
    if not isinstance(roi, (list, tuple)) or len(roi) != 4 or any(
        type(value) not in (int, float) or not 0 <= value <= 1 for value in roi
    ):
        raise _profile_error("Inspection ROI requires four normalized coordinates")
    x, y, width, height = roi
    if min(width, height) <= 0 or x + width > 1 or y + height > 1:
        raise _profile_error("Inspection ROI must fit within the reference frame")
    return _Registration(
        min_inliers=_number(config, "min_inliers", 24, 8, 2000, integer=True),
        min_inlier_ratio=_number(config, "min_inlier_ratio", 0.55, 0.25, 1),
        min_overlap=_number(config, "min_overlap", 0.82, 0.5, 1),
        min_coverage=_number(config, "min_coverage", 0.12, 0.01, 1),
        roi=(x, y, width, height),
    )


def _features(gray: Image):
    return cv2.ORB_create(nfeatures=2000, fastThreshold=12).detectAndCompute(gray, None)


def _coverage(points: NDArray, shape: tuple[int, int]) -> float:
    return cv2.contourArea(cv2.convexHull(points.astype(np.float32))) / (shape[0] * shape[1])


def validate_reference(image: bytes, thresholds: dict, declared_media_type: str | None = None) -> str:
    """Check a golden candidate and return its detected media type, matching any supplied declaration."""
    pixels, media_type, dimensions = _decode(image)
    if declared_media_type is not None and declared_media_type != media_type:
        raise _image_error("The declared JPEG or PNG media type must match the uploaded image")
    gate = _quality(pixels, dimensions, _quality_thresholds(thresholds))
    if not gate["passed"]:
        raise _image_error("Golden image did not meet the profile quality gate", reasons=gate["failures"])
    limits = _registration(thresholds)
    gray = cv2.cvtColor(_working(pixels), cv2.COLOR_BGR2GRAY)
    keypoints, descriptors = _features(gray)
    if descriptors is None or len(keypoints) < limits.min_inliers or _coverage(
        np.asarray([point.pt for point in keypoints]), gray.shape
    ) < limits.min_coverage:
        raise _image_error("Golden image needs distinctive, well-distributed features", reasons=["ALIGNMENT_FAILED"])
    return media_type


def _matches(probe: NDArray, reference: NDArray) -> list[tuple[int, int]]:
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    def filtered(first: NDArray, second: NDArray) -> dict[int, int]:
        return {
            pair[0].queryIdx: pair[0].trainIdx
            for pair in matcher.knnMatch(first, second, k=2)
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
        }

    forward, backward = filtered(probe, reference), filtered(reference, probe)
    return [(query, train) for query, train in sorted(forward.items()) if backward.get(train) == query]


def _corners(shape: tuple[int, int]) -> NDArray:
    height, width = shape
    return np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)


def _projected_area(homography: NDArray, probe_shape: tuple[int, int]) -> float | None:
    if not np.isfinite(homography).all() or abs(homography[2, 2]) < 1e-9:
        return None
    probe_corners = _corners(probe_shape)
    depths = probe_corners @ (homography[2, :2] / homography[2, 2]) + 1
    if depths.min() < 0.25 or depths.max() / depths.min() > 2:
        return None
    projected = cv2.perspectiveTransform(probe_corners.reshape(-1, 1, 2), homography).reshape(-1, 2)
    if not np.isfinite(projected).all() or not cv2.isContourConvex(projected):
        return None
    area = cv2.contourArea(projected, oriented=True)
    return area if area > 0 else None  # Reject reflections and degenerate solves.


def _roi_pixels(shape: tuple[int, int], roi: tuple[float, float, float, float]) -> tuple[slice, slice]:
    height, width = shape
    x, y, w, h = roi
    return slice(round(height * y), round(height * (y + h))), slice(round(width * x), round(width * (x + w)))


def _correlation(first: Image, second: Image, mask: NDArray[np.bool_]) -> float:
    a, b = first[mask].astype(np.float32), second[mask].astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    energy = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    return float(np.dot(a, b)) / energy if energy > 0 else 0.0


def _align(probe: Image, reference: Image, limits: _Registration) -> tuple[Image, NDArray[np.bool_]] | None:
    probe_gray = cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY)
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    key_probe, desc_probe = _features(probe_gray)
    key_reference, desc_reference = _features(reference_gray)
    if desc_probe is None or desc_reference is None:
        return None
    pairs = _matches(desc_probe, desc_reference)
    if len(pairs) < limits.min_inliers:
        return None
    src = np.array([key_probe[i].pt for i, _ in pairs], dtype=np.float32)
    dst = np.array([key_reference[j].pt for _, j in pairs], dtype=np.float32)
    # RANSAC uses a fixed sample sequence for the same ordered correspondence arrays.
    model, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0, maxIters=2000, confidence=0.995)
    if model is None or mask is None:
        return None
    inliers = mask.ravel() != 0
    count = int(inliers.sum())
    if count < limits.min_inliers or count / len(pairs) < limits.min_inlier_ratio:
        return None
    if min(_coverage(src[inliers], probe_gray.shape), _coverage(dst[inliers], reference_gray.shape)) < limits.min_coverage:
        return None
    projected_area = _projected_area(model, probe_gray.shape)
    if projected_area is None:
        return None
    height, width = reference_gray.shape
    full_mask = cv2.warpPerspective(np.full(probe_gray.shape, 255, np.uint8), model, (width, height), flags=cv2.INTER_NEAREST)
    # Measure overlap in the warped footprint. Polygon intersections can be unstable at coincident edges.
    if cv2.countNonZero(full_mask) < limits.min_overlap * max(projected_area, height * width):
        return None
    # Erode the warped footprint so interpolation and filtering do not score invalid borders.
    footprint = cv2.erode(full_mask, np.ones((7, 7), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) != 0
    crop = _roi_pixels(reference_gray.shape, limits.roi)
    usable = footprint[crop]
    if usable.size < 256 or int(usable.sum()) < max(256, limits.min_overlap * usable.size):
        return None
    # Global gray correlation rejects a numerically valid but visually unrelated registration.
    aligned = cv2.warpPerspective(probe, model, (width, height), flags=cv2.INTER_LINEAR)
    aligned_gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    if _correlation(reference_gray[crop], aligned_gray[crop], usable) < 0.50:
        return None
    valid = np.zeros(reference_gray.shape, dtype=bool)
    valid[crop] = usable
    return aligned, valid


def _difference(aligned: Image, reference: Image, valid: NDArray[np.bool_]) -> tuple[float, list[dict], Image]:
    probe_blur = cv2.GaussianBlur(aligned, (5, 5), 0)
    reference_blur = cv2.GaussianBlur(reference, (5, 5), 0)
    signed = cv2.subtract(probe_blur, reference_blur, dtype=cv2.CV_16S)
    # Compensate a small global exposure difference while preserving localized changes.
    offset = np.clip(np.median(signed[valid], axis=0), -12, 12).astype(np.int16)
    delta = np.max(np.abs(signed - offset), axis=2)
    signal = np.maximum(delta.astype(np.float32) - 12, 0) / 92
    signal[~valid] = 0
    binary = ((delta >= 28) & valid).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    binary[~valid] = 0
    n_components, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    area = int(valid.sum())
    regions = []
    for label in range(1, n_components):
        x, y, width, height, size = (int(value) for value in stats[label])
        if size < max(20, area * 0.00012):
            continue
        row, col = slice(y, y + height), slice(x, x + width)
        strength = float(signal[row, col][labels[row, col] == label].mean())
        score = min(1.0, strength) * min(1.0, sqrt(size / (area * 0.006)))
        regions.append({"x": x, "y": y, "w": width, "h": height, "score": score})
    regions.sort(key=lambda region: (-region["score"], region["y"], region["x"]))
    global_score = min(1.0, float(signal[valid].mean()) * 3)
    raw_score = max(global_score, regions[0]["score"] if regions else 0.0)
    heat = cv2.applyColorMap((signal.clip(0, 1) * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    alpha = (signal.clip(0, 1) * 0.65)[..., None]
    overlay = np.rint(aligned * (1 - alpha) + heat * alpha).astype(np.uint8)
    return raw_score, regions[:20], overlay


def _png(pixels: Image) -> bytes:
    ok, data = cv2.imencode(".png", pixels, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise _artifact_error("Inspection artifact encoding failed")
    return data.tobytes()


def evaluate(
    image: bytes, reference: bytes | None, profile: VisionProfile | None, *, evidence_source: str | None = None
) -> VisionEvidence:
    """Run the quality gate, register the verified golden image, and measure its difference."""
    capture, _, dimensions = _decode(image)
    if profile is not None and not isinstance(profile, VisionProfile):
        raise _profile_error("The inspection profile has an invalid type")
    if evidence_source is not None and (not isinstance(evidence_source, str) or evidence_source not in _SOURCES):
        raise ApiError(422, "EVIDENCE_SOURCE_INVALID", "Inspection evidence source is invalid", {
            "component": "component_vision",
            "remediation": f"Choose one of {', '.join(sorted(_SOURCES))}.",
        })
    config = profile.thresholds if profile is not None else {}
    quality = _quality_thresholds(config)
    gate = _quality(capture, dimensions, quality)
    hold = _number(config, "hold", 0.45, 0, 1)
    reject = _number(config, "reject", 0.70, 0, 1)
    if hold >= reject:
        raise _profile_error("The hold threshold must be below the reject threshold")
    calibrated = (
        profile is not None and profile.calibrated is True and profile.method_version == METHOD_VERSION
        and type(profile.calibration_version) is int and profile.calibration_version >= 1
        and {"hold", "reject"}.issubset(config)
        and isinstance(profile.reference_sha256, str) and reference is not None
    )
    analysis = {
        "method": "golden_diff", "method_version": METHOD_VERSION, "calibrated": calibrated,
        "score": None, "hold_threshold": hold, "reject_threshold": reject,
        "threshold_tightened": False, "regions": [],
    }

    def unavailable(reasons: list[str], *, has_calibration: bool = calibrated) -> VisionEvidence:
        return VisionEvidence(gate, {**analysis, "calibrated": has_calibration}, reasons, None, None, None, evidence_source)

    if not gate["passed"]:
        return unavailable(gate["failures"], has_calibration=False)
    if profile is None or not calibrated or not isinstance(reference, bytes) or len(reference) > MAX_IMAGE_BYTES:
        return unavailable(["MODEL_UNCALIBRATED"], has_calibration=False)
    if sha256(reference).hexdigest() != profile.reference_sha256:
        return unavailable(["MODEL_UNCALIBRATED"], has_calibration=False)
    try:
        golden, _, golden_dimensions = _decode(reference)
    except ApiError as exc:
        if exc.code != "INVALID_IMAGE":
            raise
        return unavailable(["MODEL_UNCALIBRATED"], has_calibration=False)
    if not _quality(golden, golden_dimensions, quality)["passed"]:
        return unavailable(["MODEL_UNCALIBRATED"], has_calibration=False)
    limits = _registration(config)
    golden = _working(golden)
    try:
        registration = _align(_working(capture), golden, limits)
    except cv2.error:
        registration = None
    if registration is None:
        return unavailable(["ALIGNMENT_FAILED"])
    aligned, valid = registration
    try:
        score, regions, overlay = _difference(aligned, golden, valid)
        aligned_png, diff_png = _png(aligned), _png(overlay)
    except cv2.error as exc:
        raise _artifact_error("Inspection artifacts could not be built") from exc
    reasons = []
    if score >= reject:
        reasons.append("SCORE_ABOVE_REJECT")
    elif score >= hold:
        reasons.append("SCORE_ABOVE_HOLD")
    analysis.update(score=score, regions=regions, threshold_tightened=config.get("tightened") is True)
    return VisionEvidence(gate, analysis, reasons, aligned_png, diff_png, score, evidence_source)
