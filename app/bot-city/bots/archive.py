"""Immutable, hash-verified snapshot pages; the control CAS is the commit point.

Staged pages are never authoritative. No TTL or deletion is allowed. Each commit
retains an audit manifest, including the original inline snapshot on migration.
"""
import hashlib
import json

from .errors import SafetyHalt

PAGE_BYTES = 128_000


class SnapshotArchive:
    def __init__(self, client, table, account_id, owner_check):
        self.client, self.table = client, table
        self.key = {"account_id": {"S": account_id}}
        self.owner_check = owner_check

    def immutable_put(self, key, payload):
        return {"Put": {
            "TableName": self.table,
            "Item": {**self.key, "record_key": {"S": key}, "payload": {"B": payload}},
            "ConditionExpression": "attribute_not_exists(record_key) OR payload = :payload",
            "ExpressionAttributeValues": {":payload": {"B": payload}},
        }}

    def stage(self, payload):
        raw = payload.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        pages = [raw[i:i + PAGE_BYTES] for i in range(0, len(raw), PAGE_BYTES)]
        writes = [self.immutable_put(f"SNAPSHOT#{digest}#{i:012d}", page)
                  for i, page in enumerate(pages)]
        for offset in range(0, len(writes), 20):
            self.client.transact_write_items(TransactItems=[self.owner_check(), *writes[offset:offset + 20]])
        return dict(storage_format=2, sha256=digest, pages=len(pages), bytes=len(raw))

    def query(self, prefix):
        request = dict(TableName=self.table, ConsistentRead=True,
                       KeyConditionExpression="account_id = :account AND begins_with(record_key, :prefix)",
                       ExpressionAttributeValues={":account": self.key["account_id"], ":prefix": {"S": prefix}})
        seen = set()
        while True:
            result = self.client.query(**request)
            yield from result.get("Items", [])
            cursor = result.get("LastEvaluatedKey")
            if not cursor:
                return
            marker = json.dumps(cursor, sort_keys=True)
            if marker in seen:
                raise SafetyHalt("archive pagination did not advance")
            seen.add(marker)
            request["ExclusiveStartKey"] = cursor

    def read(self, manifest):
        prefix = f"SNAPSHOT#{manifest['sha256']}#"
        chunks = list(self.query(prefix))
        if len(chunks) != manifest["pages"] or any(
                item["record_key"]["S"] != f"{prefix}{i:012d}" for i, item in enumerate(chunks)):
            raise SafetyHalt("archive pages are missing, duplicated or out of order")
        raw = b"".join(bytes(item["payload"]["B"]) for item in chunks)
        if len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
            raise SafetyHalt("archive integrity check failed")
        return raw.decode("utf-8")

    def revision_put(self, revision, manifest):
        return self.immutable_put(f"REVISION#{revision:020d}",
                                  json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())

    def audit(self, head, start):
        revisions = []
        for item in self.query("REVISION#"):
            revision = int(item["record_key"]["S"].split("#")[1])
            if revision > head:
                break  # A concurrent later commit is outside this pinned audit.
            if revision != start + len(revisions):
                raise SafetyHalt("archive revision history has a gap")
            manifest = json.loads(bytes(item["payload"]["B"]))
            self.read(manifest)
            revisions.append(revision)
        if not revisions or revisions[-1] != head:
            raise SafetyHalt("archive revision history is incomplete")
        return revisions
