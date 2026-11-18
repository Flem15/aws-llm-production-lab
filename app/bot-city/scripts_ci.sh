#!/usr/bin/env bash
set -euo pipefail

python -m compileall -q bots run_bots.py backtest.py report.py healthcheck.py recovery.py && \
python -m pytest && \
python backtest.py --demo --days 2 --out /tmp/bot-city-demo.csv && \
test -s /tmp/bot-city-demo.csv && \
echo "PASS: Bot City compile, safety tests, and demo backtest passed"
