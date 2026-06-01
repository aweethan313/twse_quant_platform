#!/bin/bash
# run_daily_update.sh — V9.1-P1 每日盤後自動更新（單一指令）
# 由 launchd 每天 16:30 觸發；非交易日（週末/假日）pipeline 內部自動跳過。
# 手動測試：bash run_daily_update.sh
# 指定日期：bash run_daily_update.sh 2026-06-02

# 自動偵測專案目錄（腳本所在位置），不再寫死路徑
PROJECT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT/.venv/bin/python3"
LOG="$PROJECT/data/logs/daily_update.log"

mkdir -p "$PROJECT/data/logs"

# 找不到 venv 就用系統 python3（並警告）
if [ ! -x "$PYTHON" ]; then
    echo "⚠ 找不到 venv python3（$PYTHON），改用系統 python3"
    PYTHON="$(command -v python3)"
fi

{
  echo ""
  echo "=============================="
  echo "$(date '+%Y-%m-%d %H:%M:%S') 每日更新開始"
  echo "專案：$PROJECT"
  echo "Python：$PYTHON"
  echo "=============================="

  cd "$PROJECT" || { echo "無法進入專案目錄"; exit 1; }

  # 單一指令完成所有步驟（交易日判斷在 pipeline 內部）
  "$PYTHON" -m scripts.daily_pipeline $1
  RC=$?

  if [ $RC -eq 0 ]; then
      echo "$(date '+%H:%M:%S') 每日 pipeline 結束（return=0）"
  else
      echo "$(date '+%H:%M:%S') 每日 pipeline 有錯誤（return=$RC）"
  fi
} >> "$LOG" 2>&1

# 同時把最後結果印到終端機（手動執行時看得到）
tail -8 "$LOG"
