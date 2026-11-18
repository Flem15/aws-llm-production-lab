#!/usr/bin/env python3
"""Explicit operator-only initialization/recovery; never submits or cancels broker orders."""
import argparse
import json
import threading
from pathlib import Path

from dotenv import load_dotenv

from bots.brokers import AlpacaBroker
from bots.transport import proxy_configuration
from bots.durable import configured_state
from bots.errors import SafetyHalt
from bots.lease import configured_lease
from bots.singleton import SingleInstanceLock


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--initialize", action="store_true")
    actions.add_argument("--audit-history", action="store_true")
    actions.add_argument("--migrate-storage", action="store_true")
    actions.add_argument("--import-reviewed-state", type=Path)
    actions.add_argument("--apply-reviewed-snapshot", type=Path)
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    if args.apply_reviewed_snapshot and args.expected_revision is None:
        parser.error("--expected-revision is required for recovery")
    load_dotenv()
    proxy_configuration()
    lease = configured_lease(threading.Event())
    if lease is None:
        raise SafetyHalt("operator recovery requires distributed ownership")
    complete = False
    with SingleInstanceLock():
        try:
            lease.acquire()
            lease.start()
            broker = AlpacaBroker(ownership_guard=lease.assert_owned)
            durable = configured_state(lease, broker)
            if args.audit_history:
                print(json.dumps({"verified_revisions": durable.audit_history()}))
            elif args.migrate_storage:
                state = durable.load()
                if state is None or state["status"] not in {"CLEAN", "PAUSED"} or state["pending"]:
                    raise SafetyHalt("migration requires reviewed paused state without pending orders")
                durable._verify(broker, state)
                durable._write(state)
                print(json.dumps({"verified_revisions": durable.audit_history()}))
            elif args.initialize:
                durable.initialize(broker)
            elif args.import_reviewed_state:
                durable.import_reviewed_state(broker, json.loads(args.import_reviewed_state.read_text()))
            elif args.apply_reviewed_snapshot:
                durable.recover(broker, json.loads(args.apply_reviewed_snapshot.read_text()),
                                args.expected_revision)
            else:
                state = durable.load()
                if state is None:
                    raise SafetyHalt("account is not initialized")
                report = {
                    "revision": durable.revision, "state": state,
                    "broker": broker.reconciliation_view(),
                    "review_template": {**{key: state[key] for key in ("positions", "risk", "trades")},
                                        "absent_intents": []},
                }
                print(json.dumps(report, indent=2, default=str))
            complete = True
        finally:
            try:
                if complete:
                    lease.release()
            finally:
                lease.stop()


if __name__ == "__main__":
    main()
