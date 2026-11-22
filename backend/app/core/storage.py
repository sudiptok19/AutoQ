"""Content-addressed, immutable evidence store. Files live at <dir>/ab/cd/<sha256>; the row in
`evidence` is the index. Writes are idempotent; reads re-hash the bytes (fail closed on corruption)."""
import hashlib
import os
import tempfile
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.db import models as m

MAX_BYTES = 15 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "application/pdf", "image/webp"}
SOURCES = {"LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST", "UPLOAD"}
_MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "application/pdf": (b"%PDF-",), "image/webp": (b"RIFF",)}


def _path(sha: str) -> Path:
    return Path(settings().autoq_evidence_dir) / sha[:2] / sha[2:4] / sha


def sniff(data: bytes) -> str | None:
    for mt, sigs in _MAGIC.items():
        if any(data.startswith(s) for s in sigs):
            return mt
    return None


def put_evidence(db: Session, data: bytes, media_type: str, evidence_source: str, actor_id: str | None) -> str:
    if not data:
        raise ApiError(422, "EVIDENCE_EMPTY", "Uploaded file is empty")
    if len(data) > MAX_BYTES:
        raise ApiError(413, "EVIDENCE_TOO_LARGE", f"Max {MAX_BYTES // 1_048_576} MB")
    if evidence_source not in SOURCES:
        raise ApiError(422, "EVIDENCE_SOURCE_INVALID", f"evidence_source must be one of {sorted(SOURCES)}")
    actual = sniff(data)
    if media_type not in ALLOWED or actual != media_type:
        raise ApiError(415, "INVALID_MEDIA", f"Declared {media_type!r}, content looks like {actual or 'unknown'}", {"allowed": sorted(ALLOWED)})
    sha = hashlib.sha256(data).hexdigest()
    p = _path(sha)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, p)  # atomic; identical content means a concurrent writer is harmless
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        os.chmod(p, 0o444)
    db.execute(insert(m.Evidence).values(sha256=sha, media_type=media_type, byte_size=len(data), storage_path=str(p.relative_to(settings().autoq_evidence_dir)), evidence_source=evidence_source, created_by=actor_id).on_conflict_do_nothing(index_elements=["sha256"]))
    return sha


def get_evidence(db: Session, sha: str) -> tuple[bytes, str]:
    row = db.get(m.Evidence, sha)
    if not row:
        raise ApiError(404, "EVIDENCE_NOT_FOUND", "No evidence with that digest")
    p = Path(settings().autoq_evidence_dir) / row.storage_path
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        raise ApiError(503, "EVIDENCE_STORE_UNAVAILABLE", "Evidence file missing from store", {"component": "storage", "sha256": sha})
    if hashlib.sha256(data).hexdigest() != sha:
        raise ApiError(500, "EVIDENCE_CORRUPT", "Stored bytes do not match their digest", {"component": "storage", "sha256": sha})
    return data, row.media_type
