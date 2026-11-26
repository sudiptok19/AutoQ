import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app.core import errors
from app.core.canonical import canonicalize
from app.core.signing import b64url
from app.db.session import get_db
from app.features.passport_crypto.router import router
from app.features.passport_crypto.service import (
    GENESIS,
    CryptoError,
    KeyRecord,
    build_snapshot,
    chain_hash,
    sign,
    validate_payload,
    verify,
    verify_snapshot,
)
from app.ports import ports

pytestmark = pytest.mark.nodb

VECTORS = json.loads((Path(__file__).parent / "vectors" / "aq1_interop.json").read_text(encoding="utf-8"))
TEST_SIGNING_KEY = SigningKey(bytes.fromhex(VECTORS["test_seed"]["value"]))
TEST_KEY = KeyRecord(**VECTORS["key"])
AS_OF = VECTORS["as_of"]
VALID_BY_NAME = {case["name"]: case for case in VECTORS["valid"]}
PP = VALID_BY_NAME["python_pp_unicode_text"]["payload"]
PKG = VALID_BY_NAME["python_pkg"]["payload"]
SNAP = VALID_BY_NAME["python_snap"]["payload"]
CHAIN_V2 = VECTORS["chain_v2"]
CHAIN_BY_NAME = {case["name"]: case for case in CHAIN_V2["valid"]}


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda vector: vector["name"])
def test_verified_bytes_and_signing_interoperate(vector):
    verified = verify(vector["token"], [TEST_KEY], vector["expected_type"], AS_OF)
    assert {"ok": True, "typ": verified.typ, "v": verified.v, "kid": verified.kid, "warning": verified.warning} == vector[
        "expected"
    ]
    assert verified.payload == vector["payload"]
    assert verified.payload_bytes == vector["canonical"].encode("utf-8")
    if verified.v == 2:
        reordered = dict(reversed(list(vector["payload"].items())))
        assert sign(reordered, TEST_KEY.kid, TEST_SIGNING_KEY) == vector["token"]


@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda vector: vector["name"])
def test_negative_interop_vectors(vector):
    trusted = [KeyRecord(**record) for record in vector.get("trusted_keys", [VECTORS["key"]])]
    with pytest.raises(CryptoError) as caught:
        verify(vector["token"], trusted, vector["expected_type"], vector.get("as_of", AS_OF))
    assert caught.value.code == caught.value.error_code == vector["expected"]["code"]
    assert caught.value.component == "passport_crypto"
    assert caught.value.message


@pytest.mark.parametrize(
    ("payload", "changes"),
    [
        (PP, {"iat": True}),
        (PP, {"iat": -1}),
        (PP, {"iat": 2**53}),
        (PP, {"serial": "\ud800"}),
        (PP, {"heats": []}),
        (PP, {"evidence_digests": ["f" * 64]}),
        (PP, {"authorizing_inspection_ids": []}),
        (PP, {"four_m_tagged": 0}),
        (PP, {"inspection_basis": "ESTIMATED"}),
        (PKG, {"seal": "sha256:" + "f" * 64}),
        (PKG, {"items": []}),
        (SNAP, {"exp": SNAP["iat"]}),
        (SNAP, {"expected_units": 2.0}),
        (SNAP, {"expected_units": -1}),
        (SNAP, {"keys": [{**SNAP["keys"][0], "alg": "Ed25519"}]}),
        (SNAP, {"keys": [{**SNAP["keys"][0], "status": "DISABLED"}]}),
        (SNAP, {"keys": [{**SNAP["keys"][0], "public_key": "A" * 42}]}),
        (SNAP, {"keys": [SNAP["keys"][0], SNAP["keys"][0]]}),
    ],
)
def test_issuance_requires_exact_shapes(payload, changes):
    with pytest.raises(CryptoError) as caught:
        validate_payload({**payload, **changes})
    assert caught.value.code == "SCHEMA_INVALID"


def test_sign_does_not_issue_legacy_or_change_claim_kid():
    legacy = VALID_BY_NAME["python_historical_v1"]["payload"]
    with pytest.raises(CryptoError, match="Historical") as caught:
        sign(legacy, TEST_KEY.kid, TEST_SIGNING_KEY)
    assert caught.value.code == "VERSION_UNSUPPORTED"
    with pytest.raises(CryptoError) as caught:
        sign(PP, "different-kid", TEST_SIGNING_KEY)
    assert caught.value.code == "TOKEN_MISMATCH"


@pytest.mark.parametrize(
    "payload_bytes",
    [b'{"v":2,"v":2}', b'{"x":2.0}', b'{"x":9007199254740992}', b'{"x":"\xff"}', b'{"x":"\\ud800"}'],
)
def test_original_json_bytes_must_be_canonical(payload_bytes):
    signature = TEST_SIGNING_KEY.sign(payload_bytes).signature
    with pytest.raises(CryptoError) as caught:
        verify(f"AQ1.{b64url(payload_bytes)}.{b64url(signature)}", [TEST_KEY], None, AS_OF)
    assert caught.value.code == "TOKEN_MALFORMED"


def test_envelope_rejects_whitespace_padding_aliases_and_oversized_input():
    token = VALID_BY_NAME["python_pp_unicode_text"]["token"]
    symbols = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alternative_pad_bits = symbols[symbols.index(token[-1]) + 1]
    for invalid in (f" {token}", f"{token}=", f"{token}.extra", token[:-1] + alternative_pad_bits, "x" * 2**21):
        with pytest.raises(CryptoError) as caught:
            verify(invalid, [TEST_KEY], "PP", AS_OF)
        assert caught.value.code == "TOKEN_MALFORMED"


def test_rotation_honors_issuance_and_current_key_windows():
    old_key = replace(TEST_KEY, status="RETIRED", not_before=PP["iat"], not_after=PP["iat"] + 10)
    new_signing_key = SigningKey.generate()
    new_key = KeyRecord("new-key", b64url(bytes(new_signing_key.verify_key)), "ACTIVE", AS_OF - 5, AS_OF)
    old_token = VALID_BY_NAME["python_pp_unicode_text"]["token"]
    new_claims = {**PP, "kid": new_key.kid, "iat": AS_OF}
    new_token = sign(new_claims, new_key.kid, new_signing_key)
    assert verify(old_token, [old_key, new_key], "PP", AS_OF).kid == old_key.kid
    assert verify(new_token, [old_key, new_key], "PP", AS_OF).kid == new_key.kid

    early_token = sign({**PP, "iat": old_key.not_before - 1}, old_key.kid, TEST_SIGNING_KEY)
    for token, keys, at, code in (
        (old_token, [replace(old_key, status="REVOKED"), new_key], AS_OF, "KEY_REVOKED"),
        (early_token, [old_key, new_key], AS_OF, "KEY_EXPIRED"),
        (new_token, [old_key, new_key], AS_OF + 1, "KEY_EXPIRED"),
    ):
        with pytest.raises(CryptoError) as caught:
            verify(token, keys, "PP", at)
        assert caught.value.code == code


@pytest.mark.parametrize("name", ["python_pp_unicode_text", "python_snap"])
def test_future_issuance_fails(name):
    vector = VALID_BY_NAME[name]
    with pytest.raises(CryptoError) as caught:
        verify(vector["token"], [TEST_KEY], vector["expected_type"], vector["payload"]["iat"] - 1)
    assert caught.value.code == "SCHEMA_INVALID"


def test_build_and_verify_snapshot_in_expected_scope():
    scope = {
        "shipment": SNAP["shipment"],
        "order": SNAP["order"],
        "device_id": SNAP["device_id"],
        "operator_id": SNAP["operator_id"],
        "policy_version": SNAP["policy_version"],
    }
    token = build_snapshot(
        snapshot_id=SNAP["id"],
        iat=SNAP["iat"],
        ttl_seconds=SNAP["exp"] - SNAP["iat"],
        part=SNAP["part"],
        expected_units=SNAP["expected_units"],
        allowed_packages=SNAP["allowed_packages"],
        held=SNAP["held"],
        revoked_kids=SNAP["revoked_kids"],
        keys=[TEST_KEY],
        kid=TEST_KEY.kid,
        key=TEST_SIGNING_KEY,
        **scope,
    )
    assert token == VALID_BY_NAME["python_snap"]["token"]
    expected_scope = {f"expected_{claim}": value for claim, value in scope.items()}
    assert verify_snapshot(token, [TEST_KEY], SNAP["exp"] - 1, **expected_scope).payload == SNAP
    for field in expected_scope:
        with pytest.raises(CryptoError) as caught:
            verify_snapshot(token, [TEST_KEY], AS_OF, **{**expected_scope, field: "wrong-scope"})
        assert caught.value.code == "TOKEN_MISMATCH"
    with pytest.raises(CryptoError) as caught:
        verify_snapshot(token, [TEST_KEY], SNAP["exp"], **expected_scope)
    assert caught.value.code == "SNAPSHOT_STALE"
    assert caught.value.remediation == "Reconnect and refresh the snapshot."


def test_chain_hash_matches_browser_projection():
    vector = VECTORS["chain"]
    event = vector["event"]
    assert vector["prev_hash"] == GENESIS
    assert chain_hash(GENESIS, event) == vector["hash"]
    assert chain_hash(GENESIS, {**event, "local_reasons": [], "hash": "excluded"}) == vector["hash"]
    assert chain_hash(GENESIS, {**event, "seq": 2}) != vector["hash"]
    assert chain_hash(vector["hash"], event) != vector["hash"]


def test_legacy_chain_keeps_original_projection_and_previous_hash_spelling():
    vector = VECTORS["chain"]
    event = vector["event"]
    assert chain_hash(GENESIS, {**event, "chain_version": 1, "snapshot_id": "snap-ignored"}) == vector["hash"]
    prev = "AB" * 32
    projection = {field: value for field, value in event.items() if field != "local_reasons"}
    expected = sha256((prev + "|" + canonicalize(projection)).encode("utf-8")).hexdigest()
    assert chain_hash(prev, event) == expected
    assert chain_hash(prev.lower(), event) != expected


@pytest.mark.parametrize("case", CHAIN_V2["valid"], ids=lambda case: case["name"])
def test_chain_v2_interoperability(case):
    event = case["event"]
    assert canonicalize(event) == case["canonical"]
    assert chain_hash(case["prev_hash"], event) == case["hash"]
    reversed_event = dict(reversed(list(event.items())))
    assert chain_hash(case["prev_hash"], {**reversed_event, "local_reasons": [], "hash": None}) == case["hash"]


def test_chain_v2_extends_the_original_checkpoint():
    legacy = VECTORS["chain"]
    head = chain_hash(legacy["prev_hash"], legacy["event"])
    seq = legacy["event"]["seq"]
    for case in CHAIN_V2["valid"]:
        event = case["event"]
        assert case["prev_hash"] == head and event["seq"] == seq + 1
        head = chain_hash(head, event)
        assert head == case["hash"]
        seq = event["seq"]


@pytest.mark.parametrize("case", CHAIN_V2["invalid"], ids=lambda case: case["name"])
def test_chain_v2_rejects_invalid_interoperability_vectors(case):
    base = CHAIN_BY_NAME[case["base"]]
    event = {**base["event"], **case.get("changes", {})}
    for field in case.get("omit", ()):
        del event[field]
    with pytest.raises(CryptoError) as caught:
        chain_hash(case.get("prev_hash", base["prev_hash"]), event)
    assert caught.value.code == case["code"]


@pytest.mark.parametrize("changes", [
    {"seq": 0}, {"seq": -1}, {"seq": True}, {"seq": "2"}, {"seq": 2.0},
    {"chain_version": 2.0}, {"snapshot_id": 123}, {"client_event_id": ""},
    {"shipment_id": "shipment/path"}, {"local_decision": "ACCEPTED"},
    {"token": None}, {"scanned_at": 1795300000},
])
def test_chain_v2_rejects_invalid_fields_without_coercion(changes):
    case = CHAIN_V2["valid"][0]
    with pytest.raises(CryptoError) as caught:
        chain_hash(case["prev_hash"], {**case["event"], **changes})
    assert caught.value.code == "SCHEMA_INVALID"


def test_chain_v2_snapshot_and_predecessor_mutations_change_the_digest():
    case = CHAIN_V2["valid"][0]
    prev, event = case["prev_hash"], case["event"]
    assert chain_hash(prev, {**event, "snapshot_id": "snap-vector-002"}) != case["hash"]
    assert chain_hash(GENESIS, event) != case["hash"]
    assert chain_hash(
        prev, {**event, "scanned_at": "2026-11-22T03:57:40.000+05:30"},
    ) != case["hash"]  # The same instant with different captured text is a different event.
    with pytest.raises(CryptoError) as caught:
        chain_hash(prev[:-1], event)
    assert caught.value.code == "SCHEMA_INVALID"


def test_chain_v2_digest_cannot_be_reused_as_a_legacy_digest():
    case = CHAIN_V2["valid"][0]
    event = dict(case["event"])
    del event["chain_version"]
    legacy_digest = chain_hash(case["prev_hash"], event)
    assert legacy_digest == chain_hash(case["prev_hash"], {**event, "chain_version": 1})
    assert legacy_digest != case["hash"]  # Opus must also disallow freshly recomputed v1 events after cutover.


def _client(port, session):
    app = FastAPI()
    errors.install(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[ports] = lambda: port
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def test_public_keys_uses_port_and_allowlists_metadata():
    session = object()
    public = {**VECTORS["key"], "alg": "Ed25519"}

    class FakePort:
        def list_public_keys(self, db):
            assert db is session
            return [{**public, "private_key": "private data must never appear"}]

    with _client(FakePort(), session) as client:
        response = client.get("/api/v1/keys")
    assert response.status_code == 200
    assert response.json() == {"keys": [public]}


@pytest.mark.parametrize("record_changes", [{"alg": "P-256"}, {"not_after": 0}, {"public_key": "invalid"}])
def test_keys_fails_closed_for_invalid_registry_metadata(record_changes):
    class FakePort:
        def list_public_keys(self, db):
            return [{**VECTORS["key"], "alg": "Ed25519", **record_changes}]

    with _client(FakePort(), None) as client:
        response = client.get("/api/v1/keys")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "ADAPTER_UNAVAILABLE"
    assert error["details"]["component"] == "passport_crypto"
