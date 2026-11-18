"""Sticky DynamoDB ownership: expiry halts the owner and never authorizes takeover."""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from .errors import SafetyHalt

log = logging.getLogger("lease")


class LeaseLost(SafetyHalt):
    """Ownership is uncertain or expired; further broker mutations are forbidden."""


class DynamoDbLease:
    def __init__(self, client, table_name, lease_key="alpaca-paper", *,
                 duration=30, interval=5, clock=time.monotonic, on_loss=None):
        if duration <= 0 or interval <= 0 or interval >= duration:
            raise ValueError("lease interval must be positive and shorter than duration")
        self.client = client
        self.table_name = table_name
        self.lease_key = lease_key
        self.owner = uuid.uuid4().hex
        self.duration = duration
        self.interval = interval
        self.clock = clock
        self.on_loss = on_loss
        self._deadline = 0
        self._acquired = False
        self._lost = False
        self._attempted = False
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    @property
    def key(self):
        return {"lease_key": {"S": self.lease_key}}

    def _fail(self, detail):
        self._lost = True
        self._stop.set()
        if self.on_loss:
            self.on_loss()
        raise LeaseLost(detail)

    def acquire(self):
        with self._mutex:
            if self._attempted:
                self._fail("lease acquisition cannot be retried in this process")
            self._attempted = True
            started = self.clock()
            try:
                self.client.put_item(
                    TableName=self.table_name,
                    Item={**self.key, "owner": {"S": self.owner}},
                    ConditionExpression="attribute_not_exists(lease_key)",
                )
            except Exception as exc:
                self._lost = True
                raise LeaseLost("lease unavailable or acquisition outcome unknown; operator review required") from exc
            self._acquired = True
            self._deadline = started + self.duration
            self.assert_owned()

    def assert_owned(self):
        with self._mutex:
            if self._lost or not self._acquired or self.clock() >= self._deadline:
                self._fail("DynamoDB lease lost or expired; broker mutations are disabled")

    def renew(self):
        with self._mutex:
            self.assert_owned()
            previous_deadline = self._deadline
            started = self.clock()
            try:
                self.client.update_item(
                    TableName=self.table_name, Key=self.key,
                    UpdateExpression="SET heartbeat = :heartbeat",
                    ConditionExpression="#owner = :owner",
                    ExpressionAttributeNames={"#owner": "owner"},
                    ExpressionAttributeValues={":owner": {"S": self.owner},
                                               ":heartbeat": {"S": uuid.uuid4().hex}},
                )
            except Exception as exc:
                try:
                    self._fail("lease renewal failed or outcome unknown; operator review required")
                except LeaseLost as failure:
                    raise failure from exc
            if self.clock() >= previous_deadline:
                self._fail("lease expired during renewal; it cannot be revived")
            self._deadline = started + self.duration

    def start(self):
        self.assert_owned()
        if self._thread is not None:
            raise RuntimeError("lease heartbeat already started")
        self._thread = threading.Thread(target=self._heartbeat, name="lease-heartbeat", daemon=True)
        self._thread.start()

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            try:
                self.renew()
            except LeaseLost:
                log.exception("distributed ownership lost")
                return

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()

    def release(self):
        """Called only after clean shutdown and broker reconciliation has completed."""
        self.stop()
        with self._mutex:
            self.assert_owned()
            try:
                self.client.delete_item(
                    TableName=self.table_name, Key=self.key,
                    ConditionExpression="#owner = :owner",
                    ExpressionAttributeNames={"#owner": "owner"},
                    ExpressionAttributeValues={":owner": {"S": self.owner}},
                )
            except Exception as exc:
                self._lost = True
                raise LeaseLost("lease release outcome unknown; operator review required") from exc
            self._acquired = False


def configured_lease(stop_event):
    in_ecs = bool(os.getenv("ECS_CONTAINER_METADATA_URI_V4") or
                  os.getenv("ECS_CONTAINER_METADATA_URI") or
                  os.getenv("AWS_EXECUTION_ENV", "").startswith("AWS_ECS"))
    mode = os.getenv("BOT_CITY_LEASE_MODE", "dynamodb" if in_ecs else "local")
    if mode == "local" and not in_ecs:
        return None
    if mode != "dynamodb":
        raise SafetyHalt("ECS requires BOT_CITY_LEASE_MODE=dynamodb")
    table = os.getenv("BOT_CITY_LEASE_TABLE", "").strip()
    if not table:
        raise SafetyHalt("BOT_CITY_LEASE_TABLE is required for distributed ownership")
    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
    if not region:
        raise SafetyHalt("AWS_REGION or AWS_DEFAULT_REGION is required for distributed ownership")
    import boto3
    from botocore.config import Config
    client = boto3.client("dynamodb", region_name=region, config=Config(
        connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 1},
    ))
    return DynamoDbLease(client, table, on_loss=stop_event.set)
