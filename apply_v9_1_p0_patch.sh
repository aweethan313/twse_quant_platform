#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
echo "[1/4] Checking project root..."
if [ ! -f "main.py" ] || [ ! -d "backend" ] || [ ! -d "scripts" ]; then
  echo "ERROR: 請把這個 zip 直接解壓到專案根目錄，也就是有 main.py / backend / scripts 的資料夾。"
  exit 1
fi
mkdir -p data/reports data/db
if [ ! -f "data/db/quant.db" ]; then
  echo "ERROR: 找不到 data/db/quant.db。請先確認你在正確的專案根目錄。"
  exit 1
fi
echo "[2/4] Backing up DB..."
cp "data/db/quant.db" "data/db/quant_before_v9_1_p0_$(date +%Y%m%d_%H%M%S).db"
echo "[3/4] Compiling changed Python files..."
python3 -m py_compile \
  backend/utils/trading_day.py \
  backend/utils/twse_client.py \
  backend/collectors/daily_eod.py \
  backend/v5/benchmark.py \
  backend/api_extensions.py \
  backend/v5/decision_engine.py \
  twse_ml_eval/ml_eval/data.py \
  twse_ml_eval/ml_scorer.py \
  scripts/v9_1_p0_fix.py \
  scripts/v5_daily_pipeline.py
chmod +x run_daily_update.sh || true
echo "[4/4] Running V9.1 P0 fix..."
python3 scripts/v9_1_p0_fix.py --apply
echo ""
echo "完成。報告位置：data/reports/v9_1_p0_fix_report.md"
echo "你可以用這行查看："
echo "cat data/reports/v9_1_p0_fix_report.md"
