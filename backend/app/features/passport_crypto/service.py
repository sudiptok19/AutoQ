"""AQ1 wire format, credential schemas, and verification against trusted Ed25519 keys."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Literal

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from app.core.canonical import MAX_SAFE, canonicalize
from app.core.signing import b64url

_MAX_PAYLOAD_BYTES = 1024 * 1024
_MAX_PAYLOAD_SEGMENT = (4 * _MAX_PAYLOAD_BYTES + 2) // 3
_SIGNATURE_BYTES = 64
_SIGNATURE_SEGMENT = (4 * _SIGNATURE_BYTES + 2) // 3
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_SHA256_HEX = re.compile(r"[a-fA-F0-9]{64}")
_EVIDENCE_DIGEST = re.compile(r"sha256:[a-fA-F0-9]{64}")
_KEY_STATUSES = {"ACTIVE", "RETIRED", "REVOKED"}
_KEY_FIELDS = {"kid", "public_key", "status", "not_before", "not_after"}

_TEXT_FIELDS = {
    "PP": ("unit", "serial", "lot", "part"),
    "PKG": ("label", "order", "seal", "seal_revision_id"),
    "SNAP": ("shipment", "order", "part", "device_id", "operator_id", "policy_version"),
}
_TEXT_LIST_FIELDS = {
    "PP": ("heats", "authorizing_inspection_ids", "evidence_digests"),
    "PKG": ("items",),
    "SNAP": ("allowed_packages", "held", "revoked_kids"),
}
_OTHER_FIELDS = {
    "PP": ("inspection_basis", "four_m_tagged"),
    "PKG": (),
    "SNAP": ("exp", "expected_units", "keys"),
}
_COMMON_FIELDS = {"v", "typ", "kid", "id", "iat"}

GENESIS = "0" * 64
_CHAIN_FIELDS = ("client_event_id", "seq", "token", "shipment_id", "scanned_at", "local_decision")


class CryptoError(Exception):
    def __init__(self, code: str, message: str, remediation: str | None = None):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.component = "passport_crypto"
        self.message = message
        self.remediation = remediation


@dataclass(frozen=True)
class KeyRecord:
    kid: str
    public_key: str
    status: str
    not_before: int
    not_after: int


@dataclass(frozen=True)
class VerifiedPayload:
    typ: str
    v: int
    kid: str
    payload: dict
    payload_bytes: bytes
    warning: Literal["VERSION_UNSUPPORTED"] | None = None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _uint(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE


def _canonical_bytes(value: object, code: str) -> bytes:
    try:
        return canonicalize(value).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise CryptoError(code, "Claims cannot be represented as canonical UTF-8 JSON") from exc


def _decode_base64url(encoded: str, code: str) -> bytes:
    if not isinstance(encoded, str) or not _BASE64URL.fullmatch(encoded) or len(encoded) % 4 == 1:
        raise CryptoError(code, "Expected unpadded base64url")
    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    if b64url(decoded) != encoded:
        raise CryptoError(code, "Base64url encoding is not canonical")
    return decoded


def _metadata(payload: dict) -> tuple[int, str]:
    if not isinstance(payload, dict):
        raise CryptoError("SCHEMA_INVALID", "AQ1 claims must be a JSON object")
    version, typ = payload.get("v"), payload.get("typ")
    if type(version) is not int:
        raise CryptoError("SCHEMA_INVALID", "AQ1 version must be an integer")
    if version not in (1, 2):
        raise CryptoError("VERSION_UNSUPPORTED", "AQ1 credential version is unsupported")
    if not isinstance(typ, str) or typ not in _TEXT_FIELDS:
        raise CryptoError("SCHEMA_INVALID", "AQ1 credential type is invalid")
    if not all(_text(payload.get(field)) for field in ("id", "kid")) or not _uint(payload.get("iat")):
        raise CryptoError("SCHEMA_INVALID", "AQ1 id, kid, or issuance timestamp is invalid")
    return version, typ


def _validate_legacy_claim_names(value: object) -> None:
    if isinstance(value, dict):
        for claim, child in value.items():
            if not claim.isascii():
                raise CryptoError("SCHEMA_INVALID", "AQ1 claim names must be ASCII")
            _validate_legacy_claim_names(child)
    elif isinstance(value, list):
        for child in value:
            _validate_legacy_claim_names(child)


def _snapshot_key(record: object) -> None:
    if not isinstance(record, dict) or record.keys() != _KEY_FIELDS:
        raise CryptoError("SCHEMA_INVALID", "SNAP key claims are invalid")
    validate_key_record(KeyRecord(**record))


def _public_key_bytes(public_key: str, code: str) -> bytes:
    if not isinstance(public_key, str) or len(public_key) != 43:
        raise CryptoError(code, "Ed25519 public key must encode 32 bytes")
    return _decode_base64url(public_key, code)


def validate_key_record(key: KeyRecord) -> None:
    """Validate public registry metadata in both snapshots and the keys endpoint."""
    if (
        not _text(key.kid)
        or not isinstance(key.status, str)
        or key.status not in _KEY_STATUSES
        or not _uint(key.not_before)
        or not _uint(key.not_after)
        or key.not_before > key.not_after
    ):
        raise CryptoError("SCHEMA_INVALID", "Public key metadata is invalid")
    _public_key_bytes(key.public_key, "SCHEMA_INVALID")


def _validate_v2(payload: dict, typ: str) -> None:
    text_fields, list_fields = _TEXT_FIELDS[typ], _TEXT_LIST_FIELDS[typ]
    if payload.keys() != _COMMON_FIELDS.union(text_fields, list_fields, _OTHER_FIELDS[typ]):
        raise CryptoError("SCHEMA_INVALID", f"AQ1 {typ} claims are incomplete or unexpected")
    for field in text_fields:
        if not _text(payload[field]):
            raise CryptoError("SCHEMA_INVALID", f"AQ1 {typ} {field} must be nonempty text")
    for field in list_fields:
        values = payload[field]
        if not isinstance(values, list) or not all(_text(value) for value in values):
            raise CryptoError("SCHEMA_INVALID", f"AQ1 {typ} {field} must be a list of nonempty strings")

    if typ == "PP":
        basis = payload["inspection_basis"]
        if basis not in ("INDIVIDUAL_100PCT", "SAMPLED_LOT") or type(payload["four_m_tagged"]) is not bool:
            raise CryptoError("SCHEMA_INVALID", "PP inspection basis or 4M tag is invalid")
        inspection_ids = payload["authorizing_inspection_ids"]
        digests = payload["evidence_digests"]
        if not payload["heats"] or not inspection_ids or not digests or not all(
            _EVIDENCE_DIGEST.fullmatch(digest) for digest in digests
        ):
            raise CryptoError("SCHEMA_INVALID", "PP requires heats, authorizing inspections and SHA-256 evidence digests")
    elif typ == "PKG":
        if not payload["items"] or not _SHA256_HEX.fullmatch(payload["seal"]):
            raise CryptoError("SCHEMA_INVALID", "PKG requires passport IDs and a raw SHA-256 seal digest")
    else:
        if not _uint(payload["exp"]) or payload["exp"] <= payload["iat"] or not _uint(payload["expected_units"]):
            raise CryptoError("SCHEMA_INVALID", "SNAP expiry or remaining unit count is invalid")
        if not isinstance(payload["keys"], list):
            raise CryptoError("SCHEMA_INVALID", "SNAP keys must be a list")
        key_ids = set()
        for record in payload["keys"]:
            _snapshot_key(record)
            if record["kid"] in key_ids:
                raise CryptoError("SCHEMA_INVALID", "SNAP key IDs must be unique")
            key_ids.add(record["kid"])


def _issuance_bytes(payload: dict) -> bytes:
    version, typ = _metadata(payload)
    if version != 2:
        raise CryptoError("VERSION_UNSUPPORTED", "Historical AQ1 credentials cannot be issued")
    _validate_v2(payload, typ)
    payload_bytes = _canonical_bytes(payload, "SCHEMA_INVALID")
    if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
        raise CryptoError("SCHEMA_INVALID", "AQ1 claims exceed the payload limit")
    return payload_bytes


def validate_payload(payload: dict) -> None:
    """Validate claims for issuance as an AQ1 v2 credential."""
    _issuance_bytes(payload)


def sign(payload: dict, kid: str, key: SigningKey) -> str:
    """Sign v2 claims with the signing key supplied by the issuer."""
    payload_bytes = _issuance_bytes(payload)
    if payload["kid"] != kid:
        raise CryptoError("TOKEN_MISMATCH", "Claim kid differs from the signing key ID")
    return f"AQ1.{b64url(payload_bytes)}.{b64url(key.sign(payload_bytes).signature)}"


def _parse_token(token: str) -> tuple[dict, bytes, bytes]:
    if not isinstance(token, str) or len(token) > 5 + _MAX_PAYLOAD_SEGMENT + _SIGNATURE_SEGMENT:
        raise CryptoError("TOKEN_MALFORMED", "AQ1 envelope exceeds its size limit or is not text")
    parts = token.split(".", 3)
    if len(parts) != 3 or parts[0] != "AQ1" or len(parts[1]) > _MAX_PAYLOAD_SEGMENT:
        raise CryptoError("TOKEN_MALFORMED", "Expected an AQ1 envelope with a payload and signature")
    payload_bytes = _decode_base64url(parts[1], "TOKEN_MALFORMED")
    if not _BASE64URL.fullmatch(parts[2]):
        raise CryptoError("TOKEN_MALFORMED", "Expected an unpadded base64url signature")
    if len(parts[2]) != _SIGNATURE_SEGMENT:
        raise CryptoError("SIG_INVALID", "Ed25519 signature must encode 64 bytes")
    signature = _decode_base64url(parts[2], "TOKEN_MALFORMED")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise CryptoError("TOKEN_MALFORMED", "AQ1 payload must be UTF-8 JSON") from exc
    if _canonical_bytes(payload, "TOKEN_MALFORMED") != payload_bytes:
        raise CryptoError("TOKEN_MALFORMED", "AQ1 payload bytes are not canonical")
    return payload, payload_bytes, signature


def _trusted_key(kid: str, trusted_keys: list[KeyRecord], issued_at: int, as_of: int) -> KeyRecord:
    matches = [key for key in trusted_keys if key.kid == kid]
    if len(matches) != 1:
        raise CryptoError("KEY_UNKNOWN", "AQ1 key ID does not identify one trusted key")
    key = matches[0]
    if key.status == "REVOKED":
        raise CryptoError("KEY_REVOKED", "AQ1 signing key is revoked")
    if key.status not in ("ACTIVE", "RETIRED"):
        raise CryptoError("KEY_UNKNOWN", "AQ1 signing key status is unknown")
    if (
        not _uint(key.not_before)
        or not _uint(key.not_after)
        or not key.not_before <= issued_at <= key.not_after
        or (key.status == "ACTIVE" and not key.not_before <= as_of <= key.not_after)
    ):
        raise CryptoError("KEY_EXPIRED", "AQ1 signing key is outside its validity window")
    return key


def verify(token: str, trusted_keys: list[KeyRecord], expected_type: str | None, as_of: int) -> VerifiedPayload:
    """Verify original bytes using only the supplied external trust set."""
    if not _uint(as_of):
        raise CryptoError("SCHEMA_INVALID", "AQ1 verification time must be a Unix timestamp")
    payload, payload_bytes, signature = _parse_token(token)
    version, typ = _metadata(payload)
    if version == 1:
        if expected_type is not None:
            raise CryptoError("VERSION_UNSUPPORTED", "Historical AQ1 credentials cannot satisfy a v2 check")
        _validate_legacy_claim_names(payload)
    else:
        _validate_v2(payload, typ)

    key = _trusted_key(payload["kid"], trusted_keys, payload["iat"], as_of)
    public_key = _public_key_bytes(key.public_key, "SIG_INVALID")
    try:
        VerifyKey(public_key).verify(payload_bytes, signature)
    except (BadSignatureError, ValueError) as exc:
        raise CryptoError("SIG_INVALID", "Ed25519 signature verification failed") from exc

    if expected_type is not None and typ != expected_type:
        raise CryptoError("TOKEN_MISMATCH", "AQ1 credential type differs from the expected type")
    if payload["iat"] > as_of:
        raise CryptoError("SCHEMA_INVALID", "AQ1 credential was issued after the verification time")
    if version == 2 and typ == "SNAP" and as_of >= payload["exp"]:
        raise CryptoError("SNAPSHOT_STALE", "Offline snapshot has expired", "Reconnect and refresh the snapshot.")
    return VerifiedPayload(typ, version, key.kid, payload, payload_bytes, "VERSION_UNSUPPORTED" if version == 1 else None)


def build_snapshot(
    *,
    snapshot_id: str,
    iat: int,
    ttl_seconds: int,
    shipment: str,
    order: str,
    part: str,
    device_id: str,
    operator_id: str,
    policy_version: str,
    expected_units: int,
    allowed_packages: list[str],
    held: list[str],
    revoked_kids: list[str],
    keys: list[KeyRecord],
    kid: str,
    key: SigningKey,
) -> str:
    """Sign offline scope and remaining capacity provided by the issuer."""
    if not _uint(iat) or not _uint(ttl_seconds) or ttl_seconds == 0:
        raise CryptoError("SCHEMA_INVALID", "Offline snapshot requires a valid issuance time and positive TTL")
    payload = {
        "v": 2,
        "typ": "SNAP",
        "kid": kid,
        "id": snapshot_id,
        "iat": iat,
        "exp": iat + ttl_seconds,
        "shipment": shipment,
        "order": order,
        "part": part,
        "device_id": device_id,
        "operator_id": operator_id,
        "policy_version": policy_version,
        "expected_units": expected_units,
        "allowed_packages": allowed_packages,
        "held": held,
        "revoked_kids": revoked_kids,
        "keys": [asdict(record) for record in keys],
    }
    return sign(payload, kid, key)


def verify_snapshot(
    token: str,
    trusted_keys: list[KeyRecord],
    as_of: int,
    *,
    expected_shipment: str,
    expected_order: str,
    expected_device_id: str,
    expected_operator_id: str,
    expected_policy_version: str,
) -> VerifiedPayload:
    """Authenticate a fresh snapshot and bind it to independently supplied scan scope."""
    snapshot = verify(token, trusted_keys, "SNAP", as_of)
    context = {
        "shipment": expected_shipment,
        "order": expected_order,
        "device_id": expected_device_id,
        "operator_id": expected_operator_id,
        "policy_version": expected_policy_version,
    }
    for field, expected in context.items():
        if snapshot.payload[field] != expected:
            raise CryptoError("TOKEN_MISMATCH", f"Offline snapshot {field} differs from the expected scan scope")
    return snapshot


def chain_hash(prev_hash: str, event: Mapping[str, object]) -> str:
    """Hash the six scan fields in frontend/src/lib/chain.ts against the previous head."""
    if not isinstance(prev_hash, str) or not _SHA256_HEX.fullmatch(prev_hash):
        raise CryptoError("SCHEMA_INVALID", "Previous scan hash must be 64 hex characters")
    try:
        projection = {field: event[field] for field in _CHAIN_FIELDS}
    except KeyError as exc:
        raise CryptoError("SCHEMA_INVALID", f"Scan event is missing {exc.args[0]}") from exc
    if any(not isinstance(projection[field], str) for field in _CHAIN_FIELDS if field != "seq"):
        raise CryptoError("SCHEMA_INVALID", "Scan projection claims other than seq must be strings")
    if type(projection["seq"]) is not int:
        raise CryptoError("SCHEMA_INVALID", "Scan sequence must be an integer")
    data = _canonical_bytes(projection, "SCHEMA_INVALID")
    return sha256(prev_hash.encode("ascii") + b"|" + data).hexdigest()
