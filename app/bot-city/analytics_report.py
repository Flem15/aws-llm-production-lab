"""Generate private historical and daily reports from an exported durable ledger.

No AWS/broker calls. Snapshot must be a complete materialized recovery export,
not an archive manifest or dashboard's last-30-trades display.
"""
import argparse
import hashlib
import json
from pathlib import Path
from bots.analytics import build_report
from bots.durable import validate_snapshot
from bots.ledger import _atomic_json


def generate(snapshot, output):
    content = Path(snapshot).read_bytes()
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get("state"), dict):
        raise ValueError("complete recovery export with state is required")
    state = payload["state"]
    validate_snapshot(state)
    if state.get("storage_format") or state.get("pending") or state.get("status") not in {"CLEAN", "PAUSED"}:
        raise ValueError("use a complete, reconciled CLEAN/PAUSED snapshot; uncertain operations block reporting")
    report = build_report(state["trades"], source={"kind": "durable-export",
        "revision": payload.get("revision"), "sha256": hashlib.sha256(content).hexdigest(),
        "status": state["status"], "checkpoint": state.get("checkpoint")})
    out = Path(output)
    _atomic_json(str(out / "analytics.json"), report)
    # Repeated generation is idempotent and updates corrected data for a day.
    # No scheduled AWS jobs or background trading process are created.
    for day in report["daily"]:
        daily = build_report([t for t in report["trades"] if t["day"] == day],
                             source=report["source"], as_of=report["generated_at"])
        _atomic_json(str(out / "daily" / (day + ".json")), daily)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", default="dashboard")
    args = parser.parse_args()
    report = generate(args.snapshot, args.output)
    print(f"Wrote {len(report['daily'])} daily reports and {report['summary']['trades']} closed trades. No network operations.")


if __name__ == "__main__":
    main()
