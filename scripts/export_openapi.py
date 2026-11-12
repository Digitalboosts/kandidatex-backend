"""Writes docs/openapi.json (import it into Postman / Insomnia, or feed a client generator).

    python scripts/export_openapi.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The schema doesn't depend on real settings; placeholders let the app import without a .env.
for key, value in {
    "DATABASE_URL": "sqlite+aiosqlite://",
    "JWT_SECRET": "x" * 32,
    "LIVEKIT_API_KEY": "key",
    "LIVEKIT_API_SECRET": "x" * 32,
    "LIVEKIT_HOST": "http://localhost:7880",
    "LIVEKIT_PUBLIC_URL": "ws://localhost:7880",
}.items():
    os.environ.setdefault(key, value)

from app.main import app  # noqa: E402

target = ROOT / "docs" / "openapi.json"
target.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {target}")
