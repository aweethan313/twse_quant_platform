#!/bin/zsh
# setup_mac.sh  –  Mac 一鍵環境建立腳本
# 執行方式：chmod +x setup_mac.sh && ./setup_mac.sh

set -e
CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'

echo "${CYAN}══════════════════════════════════════${NC}"
echo "${CYAN}  TWSE Quant Platform  Mac 安裝腳本   ${NC}"
echo "${CYAN}══════════════════════════════════════${NC}\n"

# ── 1. 確認 Python 3.10+ ──────────────────────
echo "🔍 檢查 Python 版本..."
if ! command -v python3 &>/dev/null; then
  echo "${RED}✗ 找不到 python3${NC}"
  echo "  請先安裝：brew install python  或  從 https://python.org 下載"
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
PY_MINOR=$(echo $PY_VER | cut -d. -f2)

echo "  Python $PY_VER"
if [[ $PY_MAJOR -lt 3 ]] || [[ $PY_MAJOR -eq 3 && $PY_MINOR -lt 10 ]]; then
  echo "${RED}✗ 需要 Python 3.10 以上，目前是 $PY_VER${NC}"
  echo "  升級：brew upgrade python"
  exit 1
fi
echo "${GREEN}✓ Python $PY_VER OK${NC}\n"

# ── 2. 建立虛擬環境 ──────────────────────────
echo "🔧 建立虛擬環境 .venv ..."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  echo "${GREEN}✓ .venv 建立完成${NC}"
else
  echo "  .venv 已存在，略過"
fi

# 啟動 venv
source .venv/bin/activate
echo "${GREEN}✓ 虛擬環境已啟動${NC}\n"

# ── 3. 安裝依賴 ──────────────────────────────
echo "📦 安裝 Python 套件..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "${GREEN}✓ 套件安裝完成${NC}\n"

# ── 4. 初始化資料庫 ──────────────────────────
echo "🗄  初始化資料庫 + 建立示範策略帳戶..."
python3 scripts/init_db.py
echo ""

# ── 5. 建立 __init__.py ─────────────────────
echo "📁 補齊套件結構..."
touch backend/__init__.py
touch backend/api/__init__.py
touch backend/collectors/__init__.py
touch backend/engine/__init__.py
touch backend/models/__init__.py
touch backend/signals/__init__.py
touch backend/strategies/__init__.py
touch backend/utils/__init__.py
touch config/__init__.py
touch scripts/__init__.py
echo "${GREEN}✓ __init__.py 補齊${NC}\n"

# ── 完成訊息 ─────────────────────────────────
echo "${CYAN}══════════════════════════════════════${NC}"
echo "${GREEN}✓ 安裝完成！${NC}\n"
echo "下一步指令（在此目錄執行）：\n"
echo "  ${CYAN}# 每次使用前先啟動 venv：${NC}"
echo "  source .venv/bin/activate\n"
echo "  ${CYAN}# 補抓近一年歷史日K（一次性，約 20 分鐘）：${NC}"
echo "  make backfill\n"
echo "  ${CYAN}# 啟動平台（排程 + Web）：${NC}"
echo "  make dev\n"
echo "  ${CYAN}# 或分開啟動：${NC}"
echo "  make scheduler &   # 排程背景"
echo "  make api           # Web: http://localhost:8000"
echo "${CYAN}══════════════════════════════════════${NC}"
