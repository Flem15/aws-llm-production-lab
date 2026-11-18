#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys

path = Path("state/health.json")
if not path.exists():
    raise SystemExit(1)

try:
    payload = json.loads(path.read_text())
    updated = datetime.fromisoformat(payload["updated"])
except Exception:
    raise SystemExit(1)

if updated.tzinfo is None:
    updated = updated.replace(tzinfo=timezone.utc)

status = payload.get("status")
if status not in {"starting", "running", "market_closed", "stopping"}:
    raise SystemExit(1)

age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
max_age = 360 if status == "market_closed" else 120
if age > max_age:
    raise SystemExit(1)

sys.exit(0)
