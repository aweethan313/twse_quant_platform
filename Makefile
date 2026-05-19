.PHONY: init backfill dev api scheduler test clean venv check

SHELL := /bin/zsh
VENV  := .venv
PY    := $(VENV)/bin/python3
PIP   := $(VENV)/bin/pip
UV    := $(VENV)/bin/uvicorn

# ── 建立虛擬環境 + 安裝依賴 + 初始化 DB ──────
init: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PY) scripts/init_db.py

# ── 建立 .venv（若已存在則跳過）────────────────
venv:
	@test -d $(VENV) || python3 -m venv $(VENV)
	@echo "✓ venv ready: $(VENV)"

# ── 補歷史資料（一次性，約 20 分鐘）────────────
backfill:
	$(PY) scripts/backfill_year.py

# ── 開發模式（排程背景 + FastAPI 前台）─────────
dev:
	@echo "▶ 啟動排程 (background)..."
	$(PY) scripts/run_scheduler.py &
	@echo "▶ 啟動 FastAPI → http://localhost:8000"
	$(UV) main:app --host 0.0.0.0 --port 8000 --reload

# ── 只啟動 FastAPI ───────────────────────────
api:
	$(UV) main:app --host 0.0.0.0 --port 8000 --reload

# ── 只啟動排程 ──────────────────────────────
scheduler:
	$(PY) scripts/run_scheduler.py

# ── 手動觸發今日 EOD ─────────────────────────
eod:
	$(PY) -c "from backend.collectors.daily_eod import run_eod; run_eod()"

# ── 手動計算分數 ─────────────────────────────
scores:
	$(PY) -c "from backend.signals.scorer import compute_scores; \
	           from config.stock_universe import UNIVERSE_CODES; \
	           compute_scores(UNIVERSE_CODES)"

# ── 手動執行策略帳戶 ─────────────────────────
run-strategies:
	$(PY) -c "from backend.engine.strategy_runner import run_all_strategies; \
	           run_all_strategies()"

# ── 測試 ────────────────────────────────────
test:
	$(VENV)/bin/pytest tests/ -v

# ── 環境檢查 ─────────────────────────────────
check:
	@echo "Python:  $$($(PY) --version)"
	@echo "pip:     $$($(PIP) --version | cut -d' ' -f1-2)"
	@echo "FastAPI: $$($(PY) -c 'import fastapi; print(fastapi.__version__)' 2>/dev/null || echo '未安裝')"
	@echo "DB path: $$($(PY) -c 'from config.settings import settings; print(settings.DB_PATH)')"

# ── 清理 ────────────────────────────────────
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
