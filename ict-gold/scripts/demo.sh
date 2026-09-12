#!/usr/bin/env bash
# Smoke test the whole toolchain end to end. Synthetic data only - this proves
# the code runs, never that the strategy works.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== tests =="
python3 -m unittest discover -s tests

echo
echo "== backtest on synthetic data =="
python3 -m ictgold demo --bars 20000 --mc 1000

echo
echo "== walk-forward on synthetic data =="
python3 -m ictgold walkforward --bars 60000 --folds 4

echo
echo "== scan (json for the future dashboard) =="
python3 -m ictgold scan --bars 3000 --window 2000
