"""Compare seal captures using provisional ORB registration and seam SSIM thresholds."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor
from struct import error as StructError

import cv2
import numpy as np
from PIL import Image, ImageOps
from skimage.metrics import structural_similarity

from app.core.errors import ApiError
from app.ports import SealProfile

METHOD_VERSION = "seal-orb-ssim-0.2"
EVIDENCE_SOURCES = frozenset({"LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 8192
WORKING_SIDE = 960
MIN_LONG_SIDE, MIN_SHORT_SIDE = 320, 240
MAX_DARK_RATIO = 0.65
MIN_INLIER_RATIO = 0.40
MIN_SEAM_COVERAGE = 0.90
MAX_UNANCHORED_CENTER_SHIFT = 0.02
MAX_UNANCHORED_FRAME_SHIFT = 0.06
SSIM_MARGIN = 5  # radius of the 7-pixel SSIM window and 5-pixel Gaussian blur
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


@dataclass(frozen=True)
class SealReference:
    quality_gate: dict
    keypoints: int
    usable: bool
    reason_codes: list[str]
    evidence_source: str | None = None


@dataclass(frozen=True)
class SealEvidence:
    result: str
    method_version: str
    metrics: dict
    quality_gate: dict
    aligned_png: bytes | None
    diff_png: bytes | None
    explanation: str
    evidence_source: str | None = None


def validate_evidence_source(evidence_source: str | None) -> None:
    if evidence_source is not None and (not isinstance(evidence_source, str) or evidence_source not in EVIDENCE_SOURCES):
        raise ApiError(422, "EVIDENCE_SOURCE_INVALID", "The seal evidence source is invalid.",
                       {"component": "seal_vision", "allowed": sorted(EVIDENCE_SOURCES),
                        "remediation": "Supply the evidence source that matches the image provenance."})


def _invalid_image(which: str) -> ApiError:
    return ApiError(422, "INVALID_IMAGE", f"The {which} is not a complete, opaque JPEG or PNG within the image limits.",
                    {"component": "seal_vision", "image": which, "remediation": "Capture a new JPEG or PNG seal image."})


def _decode(data: bytes, which: str) -> np.ndarray:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise _invalid_image(which)
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as source:
            width, height = source.size
            if max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
                raise _invalid_image(which)
            if source.format == "PNG":
                if not data.endswith(PNG_END) or getattr(source, "is_animated", False):
                    raise _invalid_image(which)
                source.verify()
            elif not data.endswith(b"\xff\xd9"):
                raise _invalid_image(which)
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as source:
            oriented = ImageOps.exif_transpose(source)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                if oriented.convert("RGBA").getchannel("A").getextrema() != (255, 255):
                    raise _invalid_image(which)
            return np.asarray(oriented.convert("L"))
    except (OSError, ValueError, SyntaxError, EOFError, StructError, Image.DecompressionBombError) as exc:
        raise _invalid_image(which) from exc


def _resize(gray: np.ndarray, max_side: int) -> np.ndarray:
    height, width = gray.shape
    if max(width, height) <= max_side:
        return gray
    scale = max_side / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)


def _gate(gray: np.ndarray, profile: SealProfile) -> dict:
    height, width = gray.shape
    measured = _resize(gray, 640)
    blur = float(cv2.Laplacian(measured, cv2.CV_64F).var())
    glare = float(np.mean(measured >= 250))
    dark = float(np.mean(measured <= 24))
    failures = []
    if max(width, height) < MIN_LONG_SIDE or min(width, height) < MIN_SHORT_SIDE:
        failures.append("IMAGE_QUALITY_RESOLUTION")
    if blur < profile.blur_min:
        failures.append("IMAGE_QUALITY_BLUR")
    if glare > profile.glare_max:
        failures.append("IMAGE_QUALITY_GLARE")
    if dark > MAX_DARK_RATIO:
        failures.append("IMAGE_QUALITY_DARK")
    return {"passed": not failures, "failures": failures, "width": width, "height": height,
            "blur_laplacian_var": round(blur, 1), "glare_ratio": round(glare, 4), "dark_ratio": round(dark, 4),
            "thresholds": {"blur_min": profile.blur_min, "glare_max": profile.glare_max, "dark_max": MAX_DARK_RATIO,
                           "min_long_side": MIN_LONG_SIDE, "min_short_side": MIN_SHORT_SIDE}}


def _validate_profile(profile: SealProfile) -> None:
    if isinstance(profile, SealProfile):
        scalars = (profile.suspicious_ssim, profile.intact_ssim, profile.blur_min, profile.glare_max)
        if (all(type(value) in (int, float) for value in scalars)
                and type(profile.min_inliers) is int and profile.min_inliers >= 4
                and 0 <= profile.suspicious_ssim < profile.intact_ssim <= 1
                and 0 < profile.blur_min < float("inf") and 0 <= profile.glare_max <= 1):
            return
    raise ApiError(422, "SEAL_PROFILE_INVALID", "The seal profile has invalid comparison thresholds.",
                   {"component": "seal_vision", "remediation": "Configure valid seal quality and comparison thresholds."})


def _seam_region(profile: SealProfile, original: tuple[int, int], resized: tuple[int, int]) -> tuple[slice, slice] | None:
    height, width = original
    fit_h, fit_w = resized
    if profile.roi is None:
        return slice(0, fit_h), slice(0, fit_w)
    if not isinstance(profile.roi, (tuple, list)) or len(profile.roi) != 4 or any(type(value) is not int for value in profile.roi):
        return None
    x, y, w, h = profile.roi
    if not (0 <= x < x + w <= width and 0 <= y < y + h <= height):
        return None
    left, right = floor(x * fit_w / width), ceil((x + w) * fit_w / width)
    top, bottom = floor(y * fit_h / height), ceil((y + h) * fit_h / height)
    if min(right - left, bottom - top) < 16:
        return None
    return slice(top, bottom), slice(left, right)


def _features(gray: np.ndarray, mask: np.ndarray | None = None) -> tuple[tuple, np.ndarray | None]:
    # Each comparison owns its detector so HTTP rechecks can run concurrently.
    return cv2.ORB_create(nfeatures=2200, fastThreshold=12).detectAndCompute(gray, mask)


def _supported(points: np.ndarray, shape: tuple[int, int], seam: tuple[slice, slice] | None = None,
               *, surround: bool = False) -> bool:
    height, width = shape
    hull = cv2.convexHull(points)
    if cv2.contourArea(hull) < 0.04 * width * height or np.any(np.ptp(points, axis=0) < (0.20 * width, 0.15 * height)):
        return False
    if seam is None:
        return True
    rows, cols = seam
    center = ((cols.start + cols.stop) / 2, (rows.start + rows.stop) / 2)
    allowance = 0 if surround else 0.10 * min(width, height)
    return cv2.pointPolygonTest(hull, center, True) >= -allowance


def _reject(gate: dict, code: str) -> dict:
    return {**gate, "passed": False, "failures": [*gate["failures"], code]}


def assess_reference(reference: bytes, profile: SealProfile, *, evidence_source: str | None = None) -> SealReference:
    validate_evidence_source(evidence_source)
    _validate_profile(profile)
    gray = _decode(reference, "reference")
    gate = _gate(gray, profile)
    count = 0
    if gate["passed"]:
        resized = _resize(gray, WORKING_SIDE)
        seam = _seam_region(profile, gray.shape, resized.shape)
        if seam is None:
            gate = _reject(gate, "REGISTRATION_FAILED")
        else:
            keypoints, _ = _features(resized)
            count = len(keypoints)
            points = np.float32([point.pt for point in keypoints])
            if count < profile.min_inliers or not _supported(points, resized.shape, seam) or float(resized[seam].std()) < 12:
                gate = _reject(gate, "TOO_FEW_KEYPOINTS")
    return SealReference(gate, count, gate["passed"], list(gate["failures"]), evidence_source)


def _distinct(matches: tuple) -> dict[int, cv2.DMatch]:
    return {pair[0].queryIdx: pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance}


def _match(ref_keys: tuple, ref_descriptors: np.ndarray, probe_keys: tuple, probe_descriptors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = _distinct(matcher.knnMatch(probe_descriptors, ref_descriptors, k=2))
    reverse = _distinct(matcher.knnMatch(ref_descriptors, probe_descriptors, k=2))
    matches = sorted((match for match in forward.values() if match.trainIdx in reverse and reverse[match.trainIdx].trainIdx == match.queryIdx),
                     key=lambda match: match.distance)
    src = np.float32([probe_keys[match.queryIdx].pt for match in matches]).reshape(-1, 2)
    dst = np.float32([ref_keys[match.trainIdx].pt for match in matches]).reshape(-1, 2)
    return src, dst


def _anchor_mask(shape: tuple[int, int], ref_shape: tuple[int, int], seam: tuple[slice, slice] | None) -> np.ndarray:
    height, width = shape
    if seam is None:
        left, right, top, bottom = round(width * 0.14), round(width * 0.86), round(height * 0.26), round(height * 0.74)
    else:
        ref_h, ref_w = ref_shape
        rows, cols = seam
        left, right = round(cols.start * width / ref_w) - 16, round(cols.stop * width / ref_w) + 16
        top, bottom = round(rows.start * height / ref_h) - 16, round(rows.stop * height / ref_h) + 16
    mask = np.full(shape, 255, np.uint8)
    mask[max(0, top):bottom, max(0, left):right] = 0
    return mask


def _anchor_features(gray: np.ndarray, mask: np.ndarray) -> tuple[tuple, np.ndarray | None]:
    keypoints, descriptors = _features(gray, mask)
    if descriptors is None:
        return keypoints, None
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    # A keypoint center outside the seam is insufficient if its descriptor
    # samples the seam itself, especially at coarser ORB pyramid levels.
    selected = [i for i, key in enumerate(keypoints) if distance[round(key.pt[1]), round(key.pt[0])] >= 0.71 * key.size]
    return tuple(keypoints[i] for i in selected), descriptors[selected] if selected else None


def _plausible(transform: np.ndarray, src_shape: tuple[int, int], dst_shape: tuple[int, int]) -> bool:
    src_h, src_w = src_shape
    dst_h, dst_w = dst_shape
    corners = np.float64([(0, 0, 1), (src_w - 1, 0, 1), (src_w - 1, src_h - 1, 1), (0, src_h - 1, 1)])
    mapped = corners @ transform.T
    denominators = mapped[:, 2]
    if not np.isfinite(mapped).all() or denominators.min() <= 0 or denominators.max() > 3 * denominators.min():
        return False
    projected = mapped[:, :2] / denominators[:, None]
    polygon = np.float32(projected / (dst_w, dst_h))
    if not np.isfinite(polygon).all() or not cv2.isContourConvex(polygon):
        return False
    area = cv2.contourArea(polygon, oriented=True)
    sides = np.linalg.norm(np.roll(polygon, -1, axis=0) - polygon, axis=1)
    edges = np.roll(projected, -1, axis=0) - projected
    scale = np.linalg.norm(edges, axis=1) / (src_w - 1, src_h - 1, src_w - 1, src_h - 1)
    return 0.30 <= area <= 3 and np.all((0.35 <= sides) & (sides <= 2.5)) and scale.max() <= 2.5 * scale.min()


def _register(src: np.ndarray, dst: np.ndarray, probe_shape: tuple[int, int], ref_shape: tuple[int, int],
              seam: tuple[slice, slice], min_inliers: int, metrics: dict, *,
              surrounding: bool = False, inferred_seam: bool = False) -> np.ndarray | None:
    metrics.update(good_matches=len(src), inliers=0, inlier_ratio=0.0)
    if len(src) < min_inliers:
        return None
    # Feature density on the printed tape can exceed that of the entire box.
    # Limit the influence of any one image region in the fit.
    ref_h, ref_w = ref_shape
    cells = (dst / (ref_w / 6, ref_h / 5)).astype(int)
    counts: dict[tuple[int, int], int] = {}
    selected = []
    for index, (col, row) in enumerate(cells):
        cell = (col, row)
        counts[cell] = counts.get(cell, 0) + 1
        if counts[cell] <= 32:
            selected.append(index)
    fit_indices = selected if len(selected) >= min_inliers else np.arange(len(src))
    try:
        transform, mask = cv2.findHomography(src[fit_indices], dst[fit_indices], cv2.RANSAC, 3.0)
    except cv2.error:
        return None
    if transform is None or mask is None or not _plausible(transform, probe_shape, ref_shape):
        return None
    aligned = cv2.perspectiveTransform(src[:, None, :], transform).reshape(-1, 2)
    inlier_mask = np.linalg.norm(aligned - dst, axis=1) <= 3.0
    inliers = int(inlier_mask.sum())
    inlier_ratio = inliers / len(src)
    metrics.update(inliers=inliers, inlier_ratio=round(inlier_ratio, 3))
    if inliers < min_inliers or inlier_ratio < MIN_INLIER_RATIO:
        return None
    ref_inliers = dst[inlier_mask]
    if not _supported(src[inlier_mask], probe_shape) or not _supported(ref_inliers, ref_shape, seam, surround=surrounding):
        return None
    if inferred_seam:
        # Without an explicit ROI, features on opposing ends of a long tape
        # can escape the central exclusion mask. Demand broad support in both axes.
        low, high = np.percentile(ref_inliers, [5, 95], axis=0)
        if np.any(high - low < (0.45 * ref_w, 0.45 * ref_h)):
            return None
    return transform


def _frame_shift(transform: np.ndarray, probe_shape: tuple[int, int], ref_shape: tuple[int, int]) -> tuple[float, float]:
    # Compare corresponding frame positions before and after projection. The
    # center measures translation and the grid also covers rotation and scale.
    probe_h, probe_w = probe_shape
    ref_h, ref_w = ref_shape
    grid = np.float32([(x, y) for y in (0.2, 0.5, 0.8) for x in (0.2, 0.5, 0.8)])
    probe_grid = grid * np.float32([probe_w - 1, probe_h - 1])
    reference_grid = grid * np.float32([ref_w - 1, ref_h - 1])
    projected = cv2.perspectiveTransform(probe_grid[:, None, :], transform).reshape(-1, 2)
    shift = np.linalg.norm(projected - reference_grid, axis=1) / min(ref_h, ref_w)
    return float(shift[4]), float(shift.max())


def _weakest_region(reference: np.ndarray, similarity: np.ndarray, valid: np.ndarray) -> float | None:
    # Overlapping tiles can find a tear that would be diluted by the rest of the seal.
    height, width = reference.shape
    side = min(48, width, height)
    stride = side // 2
    scores = []
    for top in [*range(0, height - side, stride), height - side]:
        for left in [*range(0, width - side, stride), width - side]:
            tile = np.s_[top:top + side, left:left + side]
            mask = valid[tile]
            if np.mean(mask) >= 0.90 and np.std(reference[tile][mask]) >= 20:
                scores.append(float(similarity[tile][mask].mean()))
    return min(scores, default=None)


def _png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ApiError(503, "SEAL_EVIDENCE_ENCODE_FAILED", "Could not encode the seal evidence.", {"component": "seal_vision"})
    return encoded.tobytes()


def compare(reference: bytes, probe: bytes, profile: SealProfile, *, evidence_source: str | None = None) -> SealEvidence:
    validate_evidence_source(evidence_source)
    _validate_profile(profile)
    probe_gray = _decode(probe, "probe")
    ref_gray = _decode(reference, "reference")
    gate = _gate(probe_gray, profile)
    ref_gate = _gate(ref_gray, profile)
    gate = {**gate, "reference": ref_gate, "passed": gate["passed"] and ref_gate["passed"],
            "failures": list(dict.fromkeys([*gate["failures"], *ref_gate["failures"]]))}
    regional_threshold = max(0.0, profile.suspicious_ssim - 0.15)
    metrics = {"keypoints_ref": 0, "keypoints_probe": 0, "good_matches": 0, "inliers": 0, "inlier_ratio": 0.0, "registration_basis": None,
               "surrounding_support": None, "center_shift_ratio": None, "frame_shift_ratio": None,
               "ssim": None, "min_region_ssim": None, "seam_coverage": None,
               "thresholds": {"min_inliers": profile.min_inliers, "intact_ssim": profile.intact_ssim,
                              "suspicious_ssim": profile.suspicious_ssim, "min_inlier_ratio": MIN_INLIER_RATIO,
                              "min_seam_coverage": MIN_SEAM_COVERAGE, "regional_suspicious_ssim": regional_threshold,
                              "max_unanchored_center_shift": MAX_UNANCHORED_CENTER_SHIFT,
                              "max_unanchored_frame_shift": MAX_UNANCHORED_FRAME_SHIFT}}

    def failed(explanation: str, code: str | None = None) -> SealEvidence:
        failed_gate = _reject(gate, code) if code else gate
        return SealEvidence("UNVERIFIABLE", METHOD_VERSION, metrics, failed_gate, None, None, explanation, evidence_source)

    if not gate["passed"]:
        return failed("Capture a sharper, evenly lit seal image at the required resolution." if ref_gate["passed"]
                      else "The enrolled seal image failed the quality gate and requires re-enrolment.")

    ref, candidate = _resize(ref_gray, WORKING_SIDE), _resize(probe_gray, WORKING_SIDE)
    seam = _seam_region(profile, ref_gray.shape, ref.shape)
    if seam is None:
        return failed("The enrolled seam region is invalid and requires re-enrolment.", "REGISTRATION_FAILED")
    ref_crop = ref[seam]
    ref_keys, ref_descriptors = _features(ref)
    probe_keys, probe_descriptors = _features(candidate)
    metrics.update(keypoints_ref=len(ref_keys), keypoints_probe=len(probe_keys))
    if (ref_descriptors is None or probe_descriptors is None or min(len(ref_keys), len(probe_keys)) < profile.min_inliers
            or np.std(ref_crop) < 12):
        return failed("Recapture the seal with its full printed pattern visible.", "TOO_FEW_KEYPOINTS")

    # Prefer stationary box texture outside the seam. A fit on a displaced tape
    # alone can erase the displacement being measured.
    excluded = seam if profile.roi is not None else None
    ref_anchors, ref_anchor_descriptors = _anchor_features(ref, _anchor_mask(ref.shape, ref.shape, excluded))
    probe_anchors, probe_anchor_descriptors = _anchor_features(candidate, _anchor_mask(candidate.shape, ref.shape, excluded))
    transform = None
    if (ref_anchor_descriptors is not None and probe_anchor_descriptors is not None
            and min(len(ref_anchors), len(probe_anchors)) >= profile.min_inliers):
        src, dst = _match(ref_anchors, ref_anchor_descriptors, probe_anchors, probe_anchor_descriptors)
        transform = _register(src, dst, candidate.shape, ref.shape, seam, profile.min_inliers, metrics,
                              surrounding=True, inferred_seam=excluded is None)
    surrounding_support = transform is not None
    metrics["surrounding_support"] = surrounding_support
    if transform is not None:
        metrics["registration_basis"] = "OUTSIDE_SEAM" if excluded is not None else "OUTER_FRAME"
    if transform is None:
        src, dst = _match(ref_keys, ref_descriptors, probe_keys, probe_descriptors)
        transform = _register(src, dst, candidate.shape, ref.shape, seam, profile.min_inliers, metrics)
        metrics["registration_basis"] = "DISTRIBUTED_FRAME" if transform is not None else None
    if transform is None:
        return failed("Recapture the seal from a similar angle with its full pattern visible.", "REGISTRATION_FAILED")

    center_shift, frame_shift = _frame_shift(transform, candidate.shape, ref.shape)
    metrics.update(center_shift_ratio=round(center_shift, 4), frame_shift_ratio=round(frame_shift, 4))
    size = (ref.shape[1], ref.shape[0])
    aligned = cv2.warpPerspective(candidate, transform, size)[seam]
    coverage = cv2.warpPerspective(np.full(candidate.shape, 255, np.uint8), transform, size)[seam] == 255
    footprint = np.ones((2 * SSIM_MARGIN + 1, 2 * SSIM_MARGIN + 1), np.uint8)
    valid = cv2.erode(coverage.astype(np.uint8), footprint).astype(bool)
    interior = np.s_[SSIM_MARGIN:-SSIM_MARGIN, SSIM_MARGIN:-SSIM_MARGIN]
    fraction = float(valid[interior].mean())
    metrics["seam_coverage"] = round(fraction, 3)
    if fraction < MIN_SEAM_COVERAGE:
        return failed("Recapture the image with the entire enrolled seam in view.", "REGISTRATION_FAILED")
    if not surrounding_support and (center_shift > MAX_UNANCHORED_CENTER_SHIFT or frame_shift > MAX_UNANCHORED_FRAME_SHIFT):
        return failed("Recapture with more of the surrounding box visible so the seal alignment can be verified.", "REGISTRATION_FAILED")
    # Only average SSIM windows fully supported by real probe pixels and inside the seam.
    valid[:SSIM_MARGIN] = valid[-SSIM_MARGIN:] = False
    valid[:, :SSIM_MARGIN] = valid[:, -SSIM_MARGIN:] = False
    _, similarity_map = structural_similarity(cv2.GaussianBlur(ref_crop, (5, 5), 0), cv2.GaussianBlur(aligned, (5, 5), 0),
                                               full=True, data_range=255, win_size=7)
    similarity = float(similarity_map[valid].mean())
    weakest = _weakest_region(ref_crop[interior], similarity_map[interior], valid[interior])
    metrics.update(ssim=round(similarity, 3), min_region_ssim=round(weakest, 3) if weakest is not None else None)
    if similarity < profile.suspicious_ssim or (weakest is not None and weakest < regional_threshold):
        result, explanation = "SUSPICIOUS", f"The registered seam differs from the reference (SSIM {similarity:.2f}), so refer it for quality review."
    elif similarity >= profile.intact_ssim:
        result, explanation = "INTACT", f"The registered seam matches the reference with SSIM {similarity:.2f}."
    else:
        result, explanation = "UNVERIFIABLE", "Ambiguous similarity, recapture."
    heat = cv2.applyColorMap(np.clip((1 - similarity_map) * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    display = cv2.cvtColor(aligned, cv2.COLOR_GRAY2BGR)
    diff = cv2.addWeighted(display, 0.5, heat, 0.5, 0)
    diff[~valid] = display[~valid]
    return SealEvidence(result, METHOD_VERSION, metrics, gate, _png(aligned), _png(diff), explanation, evidence_source)
