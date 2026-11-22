"""Public metadata for AQ1 verification keys."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.session import get_db
from app.features.passport_crypto.service import CryptoError, KeyRecord, validate_key_record
from app.ports import Ports, ports

router = APIRouter(tags=["passport-crypto"])


class PublicKey(BaseModel):
    kid: str
    alg: Literal["Ed25519"]
    public_key: str
    status: Literal["ACTIVE", "RETIRED", "REVOKED"]
    not_before: int
    not_after: int


class PublicKeys(BaseModel):
    keys: list[PublicKey]


@router.get("/keys")
def list_keys(db: Session = Depends(get_db), public_keys: Ports = Depends(ports)) -> PublicKeys:
    records = public_keys.list_public_keys(db)
    try:
        keys = []
        for record in records:
            metadata = {field: record[field] for field in PublicKey.model_fields}
            key = PublicKey.model_validate(metadata, strict=True)
            validate_key_record(KeyRecord(**key.model_dump(exclude={"alg"})))
            keys.append(key)
    except (CryptoError, KeyError, TypeError, ValidationError) as exc:
        raise ApiError(
            503,
            "ADAPTER_UNAVAILABLE",
            "Public signing key registry returned invalid metadata",
            {"component": "passport_crypto", "remediation": "Check the public signing key registry."},
        ) from exc
    return PublicKeys(keys=keys)
