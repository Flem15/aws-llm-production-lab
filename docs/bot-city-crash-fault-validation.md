# Prepared supervised crash-fault validation scope — NOT EXECUTABLE / NO-GO

Target only development account 655469962246, us-east-2. No AWS action was taken to prepare this package. Do not rerun initialization of the existing Alpaca account, reset either earlier execution latch, force-unlock its lease, clear records, or reuse the completed runtime authorization.

## Preserve the proven baseline

The completed supervised retry reports three successful trading-disabled tasks, clean state revision 7, no orders or positions, lease released, actual alarm receipt and return to zero. That proves clean restart, not crash recovery. Before any new test, re-read the baseline and capture the control-record version, immutable history manifest, task definitions/digests, service counts and absence of active tasks. The current paper-account state must be unchanged by every synthetic scenario.

## Smallest safe fault test

Use an explicitly reviewed synthetic harness image/task revision, an in-process broker double, and isolated synthetic account/lease/history partition keys. Do not supply Alpaca secrets or grant Secrets Manager access. Do not start the restricted proxy: a synthetic broker requires no external API. Keep trading disabled in the deployed configuration; the harness must call only its fake broker and the intended DynamoDB test partitions. No environment or command overrides to the approved production-style task are permitted.

This requires a separately reviewed task definition and exact digest, synthetic key namespace, scoped task permissions, controller permissions, egress review and a new single-use latch. None has been activated. Existing exact-revision runtime permissions must not be assumed to authorize a new harness. Pin all identifiers and checksums before requesting execution. No FIS experiment, broad permission expansion, new architecture, or real broker fault injection is proposed.

## Scenario matrix

| Fault | Injection | Required evidence and safe outcome |
| --- | --- | --- |
| Abrupt process death | Synthetic harness exits without cleanup after durable INTENT; controller waits for STOPPED | Intent and strategy decision survive; no checkpointed trade fabricated; restart cannot submit under stale ownership. ECS StopTask alone is graceful first and is not proof of an abrupt crash. |
| Broker accepted, response lost | Fake broker stores a uniquely identified acceptance then raises timeout/crashes | Exactly one accepted client ID; uncertain state remains blocked on restart; no blind resubmission or fabricated P&L. |
| Partial fill and interruption | Fake terminal partial fill persisted before death | Filled quantity and broker ID preserved; remaining position reconciled; no duplicate closed trade or guessed completion. |
| Lease loss/expiry | Harness delays renewal beyond local deadline or fails renewal in its synthetic client | Owner fails closed; successor cannot automatically seize the sticky DynamoDB lease; no broker calls after loss. |
| Archive/restart | Restart against the same synthetic control revision and immutable pages | Validate all pages/hashes, preserve every order and trade; uncertain operations require explicit recovery. |
| Controller timeout/failure | Inject failure before/after task response | Discover all run-tagged tasks, stop only them, wait for STOPPED; no retries, no remaining active tasks, preserve evidence even if cleanup fails. |

The current lease is deliberately sticky: elapsed time is not authorization to delete or take it over. A crash may intentionally leave a synthetic lease behind. Preserve it as evidence. Any subsequent recovery/removal requires separate reviewed authorization and proof that the prior owner cannot run. Avoid promising automated crash recovery where the correct outcome is an operator-required safety stop.

## Controller and evidence requirements

One attended window of at most 45 minutes; sequential tasks only; no concurrent writers. Exact number of tasks, revisions, commands, scenario order and synthetic keys must be fixed by the final harness review (not a wildcard authorization). Recheck account, MFA session/source identity, current permissions, signed build evidence and its expiry immediately before execution. The earlier evidence expiry is not extended by local testing.

Use the existing controller's bounded wait/cleanup principles and a distinct preserved execution journal. Stop on any mismatch. On failure, collect task status/logs, durable control revision, manifest/page verification, synthetic broker submission count and client IDs; stop tasks and verify 0 running/pending. Retain cleanup access until zero is established. Do not silently retry, delete the stack or clear records. All alarm actions require an explicit exact alarm scope; prior direct SNS receipt does not establish the EventBridge path.

## Required approval, in sequence

1. Review/promotion and signed publication of the harness and application changes, if needed, as a separate source/artifact approval.
2. Approve only the exact task-definition registration and necessary synthetic-partition permission changes, after positive/negative tests and inspection. No existing trading data access beyond reviewed reads.
3. Approve one bounded synthetic crash test with pinned identities and cleanup authority. No orders, cancellations, liquidation, real-account initialization, force unlocks or unattended continuation.

This package prepares the scope; it is not an executable approval request until the exact harness image, task revision, partition keys and policy diff exist. Local fault tests provide useful evidence but cannot substitute for AWS execution.
