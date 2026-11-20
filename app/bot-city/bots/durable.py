"""Conditional account snapshots and write-ahead order intent, separate from leases."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
import uuid

from .errors import SafetyHalt

TERMINAL = {"filled", "canceled", "cancelled", "rejected", "expired"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def position_totals(positions):
    totals = {}
    for position in positions.values():
        symbol = position["contract"]["symbol"]
        qty = position["qty"]
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise SafetyHalt("invalid durable position quantity")
        totals[symbol] = totals.get(symbol, 0) + qty
    return totals


def validate_snapshot(state):
    """Reject missing/invalid recovery data before enabling broker mutations."""
    from datetime import date
    from math import isfinite

    def finite(value, minimum=None):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError("non-finite recovery value")
        if minimum is not None and value < minimum:
            raise ValueError("recovery value out of range")

    try:
        if state["schema"] != 1 or state["status"] not in {
                "PAUSED", "ACTIVE", "CLEAN", "RECOVERY_REQUIRED"}:
            raise ValueError("unsupported recovery schema/status")
        datetime.fromisoformat(state["baseline"])
        for field in ("positions", "risk", "orders"):
            if not isinstance(state[field], dict):
                raise ValueError("invalid recovery mapping")
        if not isinstance(state["trades"], list) or not isinstance(state["pending"], list):
            raise ValueError("invalid recovery collection")
        position_totals(state["positions"])
        for name, position in state["positions"].items():
            if name not in state["risk"]:
                raise ValueError("position has no risk state")
            date.fromisoformat(position["contract"]["expiry"])
            datetime.fromisoformat(position["entry_time"])
            finite(position["entry_price"], 0.000001)
            finite(position["entry_fees"], 0)
            finite(position["peak"], 0.000001)
        for risk in state["risk"].values():
            if risk["day"] is not None:
                date.fromisoformat(risk["day"])
            finite(risk["day_peak"], 0)
            if risk["cooldown_until"] is not None:
                datetime.fromisoformat(risk["cooldown_until"])
            if risk["off_reason"] is not None and not isinstance(risk["off_reason"], str):
                raise ValueError("invalid off-duty state")
        for trade in state["trades"]:
            date.fromisoformat(trade["day"])
            if trade["worker"] not in state["risk"]:
                raise ValueError("trade has no risk state")
            for field in ("pnl", "return_pct", "entry_price", "exit_price"):
                finite(trade[field])
        if len(set(state["pending"])) != len(state["pending"]):
            raise ValueError("duplicate pending IDs")
        for client_id in state["pending"]:
            if client_id not in state["orders"]:
                raise ValueError("pending order missing")
    except (KeyError, TypeError, ValueError) as exc:
        raise SafetyHalt("durable recovery snapshot is invalid") from exc


class DurableState:
    def __init__(self, client, table, lease, account_id, history_table=None):
        if not lease or table == lease.table_name:
            raise SafetyHalt("durable trading state requires a separate table and distributed lease")
        self.client, self.table, self.lease = client, table, lease
        from .archive import SnapshotArchive
        if history_table and history_table in {table, lease.table_name}:
            raise SafetyHalt("history, state and lease tables must be separate")
        self.archive = SnapshotArchive(client, history_table, account_id, self._owner_check) if history_table else None
        self.manifest = None
        self.account_id = account_id
        self.key = {"account_id": {"S": account_id}}
        self.snapshot = None
        self.revision = None
        self.ready = False
        self.failed = False

    def load(self):
        self.lease.assert_owned()
        try:
            item = self.client.get_item(
                TableName=self.table, Key=self.key, ConsistentRead=True).get("Item")
            if item:
                payload = item["payload"]["S"]
                stored = json.loads(payload)
                self.manifest = stored if stored.get("storage_format") == 2 else None
                if self.manifest:
                    if not self.archive:
                        raise SafetyHalt("history table configuration is required")
                    payload = self.archive.read(self.manifest)
                self.snapshot = json.loads(payload)
                self.revision = int(item["revision"]["N"])
                validate_snapshot(self.snapshot)
                if self.snapshot["schema"] != 1 or self.snapshot["account_id"] != self.account_id:
                    raise ValueError("schema/account mismatch")
            else:
                self.snapshot, self.revision = None, None
        except Exception as exc:
            self.failed = True
            raise SafetyHalt("durable state read failed; operator review required") from exc
        return deepcopy(self.snapshot)

    def _write(self, state):
        if self.failed:
            raise SafetyHalt("durable state failure is latched for this process")
        self.lease.assert_owned()
        validate_snapshot(state)
        payload = json.dumps(state, default=str, allow_nan=False, separators=(",", ":"))
        if not self.archive and len(payload.encode()) > 300_000:
            self.failed = True
            self.ready = False
            raise SafetyHalt("durable snapshot capacity reached; reviewed archival required")
        next_revision = (self.revision or 0) + 1
        put = {
            "TableName": self.table,
            "Item": {**self.key, "revision": {"N": str(next_revision)}, "payload": {"S": payload}},
            "ConditionExpression": "attribute_not_exists(account_id)",
        }
        if self.revision is not None:
            put.update(ConditionExpression="revision = :revision",
                       ExpressionAttributeValues={":revision": {"N": str(self.revision)}})
        try:
            archive_writes = []
            manifest = None
            if self.archive:
                start = self.manifest["archive_start"] if self.manifest else (self.revision or 1)
                if self.revision and not self.manifest:
                    baseline = self.archive.stage(json.dumps(self.snapshot, default=str, allow_nan=False, separators=(",", ":")))
                    baseline["archive_start"] = start
                    archive_writes.append(self.archive.revision_put(self.revision, baseline))
                manifest = self.archive.stage(payload)
                manifest["archive_start"] = start
                put["Item"]["payload"] = {"S": json.dumps(manifest)}
                archive_writes.append(self.archive.revision_put(next_revision, manifest))
            self.client.transact_write_items(
                ClientRequestToken=uuid.uuid4().hex,
                TransactItems=[
                    {"ConditionCheck": {
                        "TableName": self.lease.table_name, "Key": self.lease.key,
                        "ConditionExpression": "#owner = :owner",
                        "ExpressionAttributeNames": {"#owner": "owner"},
                        "ExpressionAttributeValues": {":owner": {"S": self.lease.owner}},
                    }},
                    {"Put": put},
                    *archive_writes,
                ])
            self.lease.assert_owned()
        except Exception as exc:
            self.ready = False
            self.failed = True
            raise SafetyHalt("durable write failed or outcome unknown; restart requires review") from exc
        self.snapshot, self.revision = json.loads(payload), next_revision
        self.manifest = manifest

    def _owner_check(self):
        self.lease.assert_owned()
        return {"ConditionCheck": {
            "TableName": self.lease.table_name, "Key": self.lease.key,
            "ConditionExpression": "#owner = :owner",
            "ExpressionAttributeNames": {"#owner": "owner"},
            "ExpressionAttributeValues": {":owner": {"S": self.lease.owner}},
        }}

    def audit_history(self):
        self.load()
        if not self.archive or not self.manifest:
            raise SafetyHalt("account has not migrated to archived storage")
        return self.archive.audit(self.revision, self.manifest["archive_start"])

    def initialize(self, broker):
        """Explicit operator action for a flat account with no order history."""
        if self.load() is not None:
            raise SafetyHalt("durable account already initialized")
        baseline = broker.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        view = broker.reconciliation_view()
        if view["account_id"] != self.account_id or view["orders"] or view["positions"]:
            raise SafetyHalt("initialization requires a flat paper account with no order history")
        self._write(dict(schema=1, account_id=self.account_id, status="PAUSED",
                         session_owner=None, baseline=baseline, orders={}, pending=[],
                         positions={}, risk={}, trades=[], checkpoint=None, recovery=None))

    def import_reviewed_state(self, broker, reviewed):
        """Explicit migration of pre-Day-17 state after operator reconciliation."""
        if self.load() is not None:
            raise SafetyHalt("migration requires an uninitialized durable account")
        if set(reviewed) != {"positions", "risk", "trades", "order_workers"}:
            raise SafetyHalt("migration requires positions, risk, trades and order_workers")
        baseline = broker.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        view = broker.reconciliation_view()
        if view["account_id"] != self.account_id or position_totals(reviewed["positions"]) != view["positions"]:
            raise SafetyHalt("migration account/positions differ from broker")
        observed = {order["client_order_id"]: order for order in view["orders"]}
        if len(observed) != len(view["orders"]) or set(observed) != set(reviewed["order_workers"]):
            raise SafetyHalt("every broker order requires an explicit worker allocation")
        orders = {}
        for client_id, execution in observed.items():
            worker = reviewed["order_workers"][client_id]
            if execution["status"] not in TERMINAL or worker not in reviewed["risk"]:
                raise SafetyHalt("migration requires terminal orders and worker risk state")
            orders[client_id] = {
                **{field: execution[field] for field in ("client_order_id", "symbol", "qty", "side", "broker_id")},
                "worker": worker, "phase": "APPLIED", "execution": execution,
                "created_at": timestamp(), "source": "operator-reviewed-migration",
            }
        state = dict(schema=1, account_id=self.account_id, status="PAUSED",
                     session_owner=None, baseline=baseline, orders=orders, pending=[],
                     positions=reviewed["positions"], risk=reviewed["risk"], trades=reviewed["trades"],
                     checkpoint={"at": timestamp(), "owner": self.lease.owner,
                                 "broker_history": getattr(self, "broker_checkpoint", None)},
                     recovery={"migration_reviewed": True, "at": timestamp()})
        self._write(state)

    def assert_ready(self):
        self.lease.assert_owned()
        if not self.ready or self.failed or self.snapshot["session_owner"] != self.lease.owner:
            raise SafetyHalt("ownership and durable reconciliation gates have not passed")

    def halt(self, detail):
        self.ready = False
        if self.snapshot is not None and not self.failed:
            state = deepcopy(self.snapshot)
            state.update(status="RECOVERY_REQUIRED", recovery={"reason": detail, "at": timestamp()})
            self._write(state)

    def _verify(self, broker, state, recovering=False):
        view = broker.reconciliation_view()
        if view["account_id"] != self.account_id:
            raise SafetyHalt("broker account differs from durable account")
        if position_totals(state["positions"]) != view["positions"]:
            raise SafetyHalt("durable and broker positions differ")
        observed = {order["client_order_id"]: order for order in view["orders"]}
        if len(observed) != len(view["orders"]):
            raise SafetyHalt("duplicate broker client order IDs")
        expected_orders = {key for key, record in state["orders"].items()
                           if record["phase"] != "ABANDONED"}
        if set(observed) != expected_orders:
            raise SafetyHalt("broker and durable order sets differ or an intent has unknown outcome")
        for client_id, record in state["orders"].items():
            if record["phase"] == "ABANDONED":
                continue
            actual = observed[client_id]
            if actual["status"] not in TERMINAL:
                raise SafetyHalt("broker has a nonterminal order")
            for field in ("symbol", "side", "qty"):
                if actual[field] != record[field]:
                    raise SafetyHalt("broker order identity differs from recorded intent")
            if record.get("broker_id") and record["broker_id"] != actual["broker_id"]:
                raise SafetyHalt("broker order ID differs from durable record")
            if not recovering and (record["phase"] != "APPLIED" or record["execution"] != actual):
                raise SafetyHalt("durable execution/fill differs from broker")
        self.broker_checkpoint = view.get("history_checkpoint")
        return observed

    def start_session(self, broker):
        state = self.load()
        if state is None:
            raise SafetyHalt("durable state must be explicitly initialized before trading")
        try:
            self._verify(broker, state)
            if state["status"] not in {"PAUSED", "CLEAN"} or state["pending"]:
                raise SafetyHalt("unclean or incomplete prior session requires operator recovery")
            state.update(status="ACTIVE", session_owner=self.lease.owner,
                         checkpoint={"at": timestamp(), "owner": self.lease.owner,
                                 "broker_history": getattr(self, "broker_checkpoint", None)})
            self._write(state)
            self.ready = True
        except SafetyHalt as exc:
            self.halt(str(exc))
            raise

    def recover(self, broker, reviewed, expected_revision):
        """Apply an explicitly reviewed snapshot, only with verified broker truth."""
        state = self.load()
        if state is None or self.revision != expected_revision:
            raise SafetyHalt("recovery revision is stale or missing")
        if set(reviewed) != {"positions", "risk", "trades", "absent_intents"}:
            raise SafetyHalt("review must contain positions, risk, trades and absent_intents")
        for client_id in reviewed["absent_intents"]:
            record = state["orders"].get(client_id)
            if not record or record["phase"] != "INTENT" or record["broker_id"]:
                raise SafetyHalt("only an unacknowledged intent can be marked absent")
            # The operator must first prove the old process cannot resume.
            record["phase"] = "ABANDONED"
        state.update({key: deepcopy(reviewed[key]) for key in ("positions", "risk", "trades")})
        observed = self._verify(broker, state, recovering=True)
        for client_id, actual in observed.items():
            state["orders"][client_id].update(phase="APPLIED", execution=actual,
                                              broker_id=actual["broker_id"])
        state.update(status="PAUSED", session_owner=None, pending=[],
                     checkpoint={"at": timestamp(), "owner": self.lease.owner,
                                 "broker_history": getattr(self, "broker_checkpoint", None)},
                     recovery={"acknowledged_revision": expected_revision, "at": timestamp()})
        self._write(state)

    def begin_order(self, contract, qty, side, worker, context=None):
        self.assert_ready()
        if not worker or isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise SafetyHalt("order intent requires worker and positive integer quantity")
        side = str(getattr(side, "value", side))
        if side not in {"buy", "sell"}:
            raise SafetyHalt("unsupported order side")
        state = deepcopy(self.snapshot)
        held = state["positions"].get(worker)
        if side == "buy" and held:
            raise SafetyHalt("worker already has a durably recorded position")
        if side == "sell":
            if not held or held["contract"]["symbol"] != contract.symbol:
                raise SafetyHalt("sell intent has no matching durable position")
            already_filled = sum(
                state["orders"][key]["execution"]["filled_qty"]
                for key in state["pending"] if state["orders"][key]["phase"] == "TERMINAL"
            )
            if qty > held["qty"] - already_filled:
                raise SafetyHalt("sell quantity exceeds the remaining durable position")
        if side == "buy" and any(
                name != worker and position["contract"]["symbol"] == contract.symbol
                for name, position in state["positions"].items()):
            raise SafetyHalt("another worker already owns this contract")
        for client_id in state["pending"]:
            previous = state["orders"][client_id]
            if (previous["phase"] != "TERMINAL" or side != "sell" or
                    previous["side"] != side or previous["worker"] != worker or
                    previous["symbol"] != contract.symbol):
                raise SafetyHalt("prior operation has not been durably checkpointed")
        client_id = "botcity-" + uuid.uuid4().hex
        state["orders"][client_id] = {
            "client_order_id": client_id, "worker": worker, "contract": asdict(contract),
            "symbol": contract.symbol, "qty": qty, "side": side, "phase": "INTENT",
            "broker_id": None, "execution": None, "created_at": timestamp(),
            "decision": deepcopy(context or {}),
        }
        state["pending"].append(client_id)
        self._write(state)
        return client_id

    def observe(self, client_id, execution):
        self.assert_ready()
        state = deepcopy(self.snapshot)
        record = state["orders"][client_id]
        if record["phase"] == "TERMINAL" and record["execution"] == execution:
            return
        if record["phase"] not in {"INTENT", "SUBMITTED", "CANCEL_REQUESTED"}:
            raise SafetyHalt("invalid durable order transition")
        for field in ("client_order_id", "symbol", "side", "qty"):
            if record[field] != execution[field]:
                raise SafetyHalt("broker result does not match durable intent")
        if record["broker_id"] and record["broker_id"] != execution["broker_id"]:
            raise SafetyHalt("broker order ID changed")
        old = record.get("execution")
        if old and execution["filled_qty"] < old["filled_qty"]:
            raise SafetyHalt("broker cumulative fill quantity decreased")
        phase = "TERMINAL" if execution["status"] in TERMINAL else "SUBMITTED"
        record.update(phase=phase, broker_id=execution["broker_id"], execution=execution)
        self._write(state)

    def cancellation(self, client_id):
        self.assert_ready()
        state = deepcopy(self.snapshot)
        record = state["orders"][client_id]
        if record["phase"] != "SUBMITTED":
            raise SafetyHalt("cancel requires a known submitted order")
        record["phase"] = "CANCEL_REQUESTED"
        record["cancel_requested_at"] = timestamp()
        self._write(state)

    def checkpoint(self, positions, risk, trades):
        self.assert_ready()
        state = deepcopy(self.snapshot)
        expected = position_totals(state["positions"])
        for client_id in state["pending"]:
            record = state["orders"][client_id]
            if record["phase"] != "TERMINAL":
                raise SafetyHalt("cannot checkpoint an uncertain order")
            sign = 1 if record["side"] == "buy" else -1
            symbol = record["symbol"]
            expected[symbol] = expected.get(symbol, 0) + sign * record["execution"]["filled_qty"]
            record["phase"] = "APPLIED"
        expected = {symbol: qty for symbol, qty in expected.items() if qty}
        if expected != position_totals(positions):
            raise SafetyHalt("position checkpoint does not match recorded executions")
        from .analytics import unique_trades, JournalConflict
        completed = deepcopy(trades)
        for trade in completed:
            if trade.get("reconciliation_status") == "pending_checkpoint":
                previous = next((t for t in state["trades"] if t.get("trade_id") and t.get("trade_id") == trade.get("trade_id")), None)
                if previous is None:
                    exits = trade.get("executions", {}).get("exit", [])
                    ids = [order["client_order_id"] for order in exits]
                    if not ids or len(ids) != len(set(ids)) or any(key not in state["pending"] for key in ids):
                        raise SafetyHalt("trade lacks unique pending exit executions")
                    records = [state["orders"][key] for key in ids]
                    if any(record["worker"] != trade["worker"] or record["side"] != "sell" or
                           record["symbol"] != trade["contract"] for record in records):
                        raise SafetyHalt("trade does not match its exit executions")
                    if sum(record["execution"]["filled_qty"] for record in records) != trade["qty"]:
                        raise SafetyHalt("trade quantity differs from terminal exit fills")
                trade["reconciliation_status"] = "checkpointed"
        try:
            combined = unique_trades(state["trades"] + completed)
        except (JournalConflict, KeyError, ValueError) as exc:
            raise SafetyHalt("trade journal conflicts with committed history") from exc
        state.update(positions=positions, risk=risk, pending=[], trades=combined)
        self._write(state)

    def finish_session(self, broker):
        self.assert_ready()
        state = deepcopy(self.snapshot)
        self._verify(broker, state)
        if state["pending"] or state["positions"]:
            raise SafetyHalt("clean shutdown requires a flat, reconciled account")
        state.update(status="CLEAN", session_owner=None,
                     checkpoint={"at": timestamp(), "owner": self.lease.owner,
                                 "broker_history": getattr(self, "broker_checkpoint", None)})
        self._write(state)
        self.ready = False


def configured_state(lease, broker):
    table = os.getenv("BOT_CITY_STATE_TABLE", "").strip()
    if not lease or not table:
        raise SafetyHalt("paper runtime requires a distributed lease and BOT_CITY_STATE_TABLE")
    history = os.getenv("BOT_CITY_HISTORY_TABLE", "").strip()
    if not history:
        raise SafetyHalt("paper runtime requires BOT_CITY_HISTORY_TABLE")
    return DurableState(lease.client, table, lease, broker.paper_account_id(), history)
