#!/bin/bash
# run_daily_update.sh — 每日盤後自動更新
# 由 launchd 每天 16:30 觸發；週末自動跳過。
# 手動測試：bash run_daily_update.sh

# V9.1-P0：用 script 所在目錄當專案根目錄，避免 launchd 指到舊資料夾。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$SCRIPT_DIR"
PYTHON="$PROJECT/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi
LOG="$PROJECT/data/logs/daily_update.log"

mkdir -p "$PROJECT/data/logs"
exec >> "$LOG" 2>&1   # 所有輸出都寫進 log

echo ""
echo "=============================="
echo "$(date '+%Y-%m-%d %H:%M:%S') 每日更新開始"
echo "=============================="

# 週末跳過（台股不開市）
DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    echo "$(date '+%H:%M:%S') 今天是週末，略過"
    exit 0
fi

cd "$PROJECT" || { echo "無法進入專案目錄"; exit 1; }

# ── 步驟 1：收盤資料（OHLCV + 籌碼 + 法人）──
echo "$(date '+%H:%M:%S') [1/4] 抓收盤資料..."
"$PYTHON" -c "from backend.collectors.daily_eod import run_eod; run_eod()"
if [ $? -ne 0 ]; then echo "⚠ 收盤資料失敗，繼續後續步驟"; fi

# ── 步驟 2：技術指標（只算今天，快）──
echo "$(date '+%H:%M:%S') [2/4] 更新技術指標..."
"$PYTHON" -m scripts.build_technical_daily_features
if [ $? -ne 0 ]; then echo "⚠ 技術指標失敗，繼續後續步驟"; fi

# ── 步驟 3：評分流程（daily_scores）──
echo "$(date '+%H:%M:%S') [3/4] 執行評分流程..."
"$PYTHON" scripts/v4_3_run_daily_workflow.py
if [ $? -ne 0 ]; then echo "⚠ 評分流程失敗，繼續後續步驟"; fi

# ── 步驟 4：ML 分數更新 ──
echo "$(date '+%H:%M:%S') [4/4] 更新 ML 分數..."
cd "$PROJECT/twse_ml_eval"
"$PYTHON" ml_scorer.py --db ../data/db/quant.db --mode latest --score-days 1
if [ $? -ne 0 ]; then echo "⚠ ML 分數更新失敗"; fi

echo "$(date '+%H:%M:%S') ✅ 每日更新完成"
