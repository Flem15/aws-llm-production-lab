# Trade intelligence and performance review

This is local implementation, not authorization for trading or deployment. The existing AWS images and runtime configuration are unchanged. The first supervised AWS test demonstrated startup and clean restart with trading disabled; deliberate crash recovery in AWS remains unverified.

## Journal and recovery

New entries receive a stable trade ID before the broker request. Strategy name and parameters, signal, observed bar context, budget and risk limits are copied into the durable order intent before submission. Positions retain this context through checkpoint/restart. A closed trade links entry and exit order records (client IDs, broker IDs, cumulative fills and terminal status), reasons, recorded fees, net realized P&L, and reconciliation status.

`checkpointed` means position accounting matched the recorded terminal executions and the conditional durable checkpoint succeeded. It is not a claim that a subsequent independent broker reconciliation has occurred. Uncertain operations still halt before checkpoint. `finish_session` still requires broker reconciliation and a flat account. Partial exits retain every component execution but produce one closed-trade result. Replays with the same identity and contents count once; conflicting contents fail closed. No order retry, ownership or recovery gate has been relaxed.

Legacy data is retained. Missing historical signals, strategy identity and reconciliation evidence cannot be reconstructed; reports flag missing evidence rather than fabricate it. SQLite receives an additive transactional `trade_journal` table, leaving `trades` intact. Do not run older writers against this upgraded SQLite file: they do not update the new journal. Back up a local database before upgrade. DynamoDB remains authoritative for paper runtime; SQLite is not a replacement.

## Reports and private dashboard

The existing ledger snapshot loop automatically produces `dashboard/analytics.json` and one `dashboard/daily/YYYY-MM-DD.json` per recorded trading day, plus an empty current-day report when there are no trades. Closed-trade history is read from the entire committed ledger, never the last-30 display. Regeneration is idempotent. These are derived local files; the archived DynamoDB ledger remains the recovery source. Daily reports are refreshed while the process runs; no cloud scheduler has been created.

For an already obtained, complete recovery export:

```sh
python analytics_report.py --snapshot /absolute/path/to/reconciled-export.json --output dashboard
python -m bots.dashboard
```

Open `http://127.0.0.1:8050/analytics.html`. The server only exposes approved dashboard files on loopback, rejects foreign Host/Origin requests, directory traversal, symlinks and mutations. It has no broker, AWS, or trade-control endpoints. Do not bind it externally or port-forward it to a public host. Loopback is a local-machine boundary, not multi-user authentication. No remote dependencies or third-party analytics are loaded.

The exporter does not read AWS or acquire a lease. It requires a materialized CLEAN/PAUSED snapshot, rejects pending operations, and records the input SHA-256 and revision. Obtain any new AWS export through separately reviewed read-only tooling. Do not run `recovery.py` merely to refresh the dashboard: that operator tool acquires a lease and has write-capable recovery modes.

Metrics: realized USD P&L after recorded fees; wins divided by all closed trades (break-even separately counted); expectancy = net P&L / trades; profit factor = gross positive P&L / absolute gross negative P&L; absolute drawdown of sequential closed-trade P&L from an initial zero peak. Profit factor is undefined when there are no losses, never serialized as Infinity. No mark-to-market drawdown, account-capital return, slippage benchmark or statistical proof of strategy profitability is claimed. Day grouping uses the stored session date. Legacy timestamps without timezone are flagged. Strategy/day filters and full journal detail are available; the journal paginates its display, not its underlying calculations.

Generated reports are excluded from Git and the Docker build context. Do not commit account exports or credentials. Local HTML is not an externally hosted AWS dashboard. New application code needs normal review, CI, signed builds and separately authorized publication/deployment before it can be used in AWS.

## Local checks

```sh
python -m pytest
python backtest.py --demo --days 2 --out /tmp/bot-city-analytics-demo.csv
```

All broker activity in these tests is simulated. AWS crash tests and any paper order require separate authorization.
