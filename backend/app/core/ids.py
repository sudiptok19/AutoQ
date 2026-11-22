import os
import time

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """Time-sortable 26-char ULID; sorting ids ascending gives a stable global lock order."""
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_B32[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid().lower()}"
