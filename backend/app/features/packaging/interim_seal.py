"""INTERIM seal vision (Opus) — same signatures as ASTRA_BRIEF §A2 (`assess_reference`, `compare`), replaced by
Astra's `seal_vision.service` when it merges. Real OpenCV ORB → Lowe ratio → RANSAC homography → SSIM.
Fails safe: anything that does not register cleanly is UNVERIFIABLE, never SUSPICIOUS or INTACT."""
from dataclasses import dataclass

import cv2
import numpy as np
from skimage.metrics import structural_similarity

from app.core.errors import ApiError
from app.features.inspection_passports.interim_vision import quality_gate
from app.ports import SealProfile

METHOD_VERSION = "interim-seal-0.1"
MAX_SIDE = 960


@dataclass(frozen=True)
class SealReference:
    quality_gate: dict
    keypoints: int
    usable: bool
    reason_codes: list[str]


@dataclass(frozen=True)
class SealEvidence:
    result: str
    method_version: str
    metrics: dict
    quality_gate: dict
    aligned_png: bytes | None
    diff_png: bytes | None
    explanation: str


def _gray(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE) if data else None
    if img is None:
        raise ApiError(422, "INVALID_IMAGE", "Seal image could not be decoded", {"component": "seal.interim"})
    s = MAX_SIDE / max(img.shape)
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img


def _gate(data: bytes, p: SealProfile) -> dict:
    return quality_gate(data, {"blur_min": p.blur_min, "glare_max": p.glare_max, "min_w": 320, "min_h": 240})


_orb = cv2.ORB_create(nfeatures=2000)


def assess_reference(reference: bytes, profile: SealProfile) -> SealReference:
    gate = _gate(reference, profile)
    kp = _orb.detect(_gray(reference), None)
    codes = list(gate["failures"]) + ([] if len(kp) >= 4 * profile.min_inliers else ["TOO_FEW_KEYPOINTS"])
    return SealReference(gate, len(kp), not codes, codes)


def _png(a: np.ndarray) -> bytes:
    return cv2.imencode(".png", a)[1].tobytes()


def compare(reference: bytes, probe: bytes, profile: SealProfile) -> SealEvidence:
    th = {"min_inliers": profile.min_inliers, "intact_ssim": profile.intact_ssim, "suspicious_ssim": profile.suspicious_ssim}
    gate = _gate(probe, profile)
    base = {"keypoints_ref": 0, "keypoints_probe": 0, "good_matches": 0, "inliers": 0, "inlier_ratio": 0.0, "ssim": None, "min_region_ssim": None, "thresholds": th}

    def unverifiable(code: str, why: str, metrics: dict) -> SealEvidence:
        g = {**gate, "passed": False, "failures": list(dict.fromkeys([*gate["failures"], code]))}
        return SealEvidence("UNVERIFIABLE", METHOD_VERSION, metrics, g, None, None, f"{why} Recapture — this is not evidence of tampering or of an intact seal.")

    if not gate["passed"]:
        return unverifiable(gate["failures"][0], f"Capture rejected ({', '.join(gate['failures'])}).", base)
    ref, prb = _gray(reference), _gray(probe)
    k1, d1 = _orb.detectAndCompute(ref, None)
    k2, d2 = _orb.detectAndCompute(prb, None)
    m = {**base, "keypoints_ref": len(k1), "keypoints_probe": len(k2)}
    if d1 is None or d2 is None or len(k1) < profile.min_inliers or len(k2) < profile.min_inliers:
        return unverifiable("TOO_FEW_KEYPOINTS", f"Too little seal texture ({len(k2)} keypoints).", m)
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d2, d1, k=2)
    good = [a for a, b in (p for p in pairs if len(p) == 2) if a.distance < 0.75 * b.distance]
    m["good_matches"] = len(good)
    if len(good) < profile.min_inliers:
        return unverifiable("REGISTRATION_FAILED", f"Only {len(good)} reliable matches.", m)
    src = np.float32([k2[g.queryIdx].pt for g in good]).reshape(-1, 1, 2)
    dst = np.float32([k1[g.trainIdx].pt for g in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    m |= {"inliers": inliers, "inlier_ratio": round(inliers / len(good), 3)}
    if H is None or inliers < profile.min_inliers or abs(np.linalg.det(H[:2, :2])) < 0.2 or abs(np.linalg.det(H[:2, :2])) > 5:
        return unverifiable("REGISTRATION_FAILED", f"Seal pattern did not register ({inliers} inliers).", m)
    aligned = cv2.warpPerspective(prb, H, (ref.shape[1], ref.shape[0]))
    valid = cv2.warpPerspective(np.full(prb.shape, 255, np.uint8), H, (ref.shape[1], ref.shape[0])) > 0
    x, y, w, h = profile.roi or (0, 0, ref.shape[1], ref.shape[0])
    r, a, v = ref[y:y + h, x:x + w], aligned[y:y + h, x:x + w], valid[y:y + h, x:x + w]
    if v.mean() < 0.9:
        return unverifiable("REGISTRATION_FAILED", "Probe does not cover the enrolled seam region.", m)
    score, smap = structural_similarity(cv2.GaussianBlur(r, (5, 5), 0), cv2.GaussianBlur(a, (5, 5), 0), full=True, data_range=255)
    bs = 48
    # SSIM is only meaningful where the reference has structure; flat cardboard blocks are dominated by sensor noise.
    blocks = [float(smap[j:j + bs, i:i + bs].mean()) for j in range(0, smap.shape[0] - bs + 1, bs) for i in range(0, smap.shape[1] - bs + 1, bs)
              if float(r[j:j + bs, i:i + bs].std()) >= 25.0]
    worst = min(blocks) if blocks else float(score)
    m |= {"ssim": round(float(score), 3), "min_region_ssim": round(worst, 3)}
    heat = cv2.applyColorMap(np.clip((1 - smap) * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    diff = cv2.addWeighted(cv2.cvtColor(a, cv2.COLOR_GRAY2BGR), 0.5, heat, 0.5, 0)
    if score >= profile.intact_ssim and worst >= profile.suspicious_ssim:
        result, why = "INTACT", f"Registered with {inliers} inliers; SSIM {score:.2f} ≥ {profile.intact_ssim}, weakest region {worst:.2f}."
    elif score < profile.suspicious_ssim or worst < profile.suspicious_ssim - 0.15:
        result, why = "SUSPICIOUS", f"Registered with {inliers} inliers but the seam differs (SSIM {score:.2f}, weakest region {worst:.2f}). Escalate to QE."
    else:
        return SealEvidence("UNVERIFIABLE", METHOD_VERSION, m, gate, _png(a), _png(diff), f"Ambiguous similarity (SSIM {score:.2f}). Recapture with even light.")
    return SealEvidence(result, METHOD_VERSION, m, gate, _png(a), _png(diff), why)
