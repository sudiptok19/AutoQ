"""Canonical JSON (CONTRACT §6/§10.3) — byte-identical to frontend/src/lib/canonical.ts."""
import json
from typing import Any

MAX_SAFE = 2**53 - 1


def canonicalize(v: Any) -> str:
    if v is None or isinstance(v, bool):
        return json.dumps(v)
    if isinstance(v, int):
        if abs(v) > MAX_SAFE:
            raise ValueError("integer outside JS-safe range")
        return str(v)
    if isinstance(v, float):
        raise ValueError("canonical JSON forbids floats")
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(canonicalize(x) for x in v) + "]"
    if isinstance(v, dict):
        items = sorted((k, x) for k, x in v.items() if x is not ...)
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{canonicalize(x)}" for k, x in items) + "}"
    raise ValueError(f"cannot canonicalize {type(v).__name__}")


def int_safe(v: Any) -> Any:
    """Make arbitrary audit data canonical-safe: non-integer numbers become strings."""
    if isinstance(v, float):
        return int(v) if v.is_integer() else repr(v)
    if isinstance(v, dict):
        return {str(k): int_safe(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [int_safe(x) for x in v]
    return v
