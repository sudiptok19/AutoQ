"""Create the Ed25519 signing seed if absent (dev). Never commits or prints the private key."""
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nacl.signing import SigningKey  # noqa: E402

from app.core.config import settings  # noqa: E402

p = settings().autoq_signing_key_path
if p.exists():
    print(f"key exists: {p} (kid {settings().autoq_signing_kid})")
    sys.exit(0)
p.parent.mkdir(parents=True, exist_ok=True)
seed = bytes(SigningKey.generate())
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(base64.urlsafe_b64encode(seed).rstrip(b"=").decode())
pub = base64.urlsafe_b64encode(bytes(SigningKey(seed).verify_key)).rstrip(b"=").decode()
print(f"created {p} (kid {settings().autoq_signing_kid}, public {pub})")
