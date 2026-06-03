#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] Checking project root..."
if [ ! -f "data/db/quant.db" ]; then
  echo "ERROR: data/db/quant.db not found. Please run this from project root."
  exit 1
fi
if [ ! -f "scripts/v9_1_p0b_fix_0050_benchmark.py" ]; then
  echo "ERROR: scripts/v9_1_p0b_fix_0050_benchmark.py not found. Did you unzip the patch?"
  exit 1
fi

echo "[2/4] Compiling changed Python files..."
python3 -m py_compile scripts/v9_1_p0b_fix_0050_benchmark.py backend/v5/benchmark.py

echo "[3/4] Running 0050 benchmark repair..."
python3 scripts/v9_1_p0b_fix_0050_benchmark.py --apply

echo "[4/4] Quick validation..."
sqlite3 data/db/quant.db <<'SQL'
SELECT '0050 suspended rows in ohlcv_daily', COUNT(*)
FROM ohlcv_daily
WHERE code='0050' AND trade_date BETWEEN '2025-06-11' AND '2025-06-17';

SELECT 'benchmark max abs daily_return', ROUND(MAX(ABS(daily_return)), 4)
FROM benchmark_daily_equity
WHERE benchmark_code='0050' AND snap_date>='2025-01-01';
SQL

echo "完成。報告位置：data/reports/v9_1_p0b_0050_benchmark_report.md"
echo "你可以用這行查看："
echo "cat data/reports/v9_1_p0b_0050_benchmark_report.md"
