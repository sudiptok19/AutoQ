"""Write contracts/openapi.json from the mounted app (Opus). Never touches ws_events.schema.json (Astra)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app  # noqa: E402

out = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
