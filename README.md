# 台股量化研究平台 TWSE Quant Platform

本機單機部署 · FastAPI + HTML/Tailwind/Chart.js · TWSE/MOPS 官方 Open Data

---

## 系統架構總覽

```
quant_platform/
├── backend/
│   ├── api/              # FastAPI 路由層
│   │   ├── routes_market.py      # 行情 / K線 API
│   │   ├── routes_signals.py     # 分數 / 信號 API
│   │   ├── routes_strategy.py    # 策略帳戶 CRUD
│   │   └── routes_intraday.py    # 盤中即時資料
│   ├── collectors/       # 資料收集排程
│   │   ├── daily_eod.py          # 每日收盤後更新（21:00 cron）
│   │   ├── intraday_tick.py      # 盤中每分鐘收集（09:00-13:30）
│   │   ├── fundamental.py        # 基本面 / 財報 / 月營收
│   │   ├── chips.py              # 籌碼：三大法人/融資融券
│   │   └── news_events.py        # 新聞 / 法說會 / 重大事件
│   ├── signals/          # 分數計算引擎
│   │   ├── fundamental_score.py  # 基本面估值分數
│   │   ├── chip_score.py         # 籌碼分數
│   │   ├── momentum_score.py     # 量價 / 內外盤分數
│   │   ├── macro_score.py        # 台指期 / 美股 / 半導體分數
│   │   └── news_score.py         # 新聞 NLP 分數（關鍵字加權）
│   ├── strategies/       # 策略定義
│   │   ├── base_strategy.py      # 抽象基底類別
│   │   ├── rule_based/           # 規則式策略
│   │   │   ├── momentum_breakout.py
│   │   │   ├── value_reversion.py
│   │   │   └── chip_follow.py
│   │   └── ml_based/             # ML 策略
│   │       ├── lgbm_ranker.py
│   │       └── feature_builder.py
│   ├── engine/           # 核心執行引擎
│   │   ├── paper_account.py      # 紙上交易帳戶
│   │   ├── order_executor.py     # 模擬撮合
│   │   ├── scheduler.py          # APScheduler 排程
│   │   └── backtest.py           # 回測引擎（Vectorbt 整合）
│   ├── models/           # SQLAlchemy ORM
│   │   ├── database.py           # SQLite 連線
│   │   ├── market.py             # 日K / 分K 表
│   │   ├── signals_db.py         # 分數快取表
│   │   ├── strategy_db.py        # 策略帳戶表
│   │   └── trade_log.py          # 交易紀錄表
│   └── utils/
│       ├── twse_client.py        # TWSE/MOPS HTTP 封裝
│       ├── cache.py              # Redis-free 本機 cache
│       └── logger.py
├── frontend/
│   ├── static/
│   │   ├── css/app.css           # Tailwind CDN + 自訂
│   │   └── js/
│   │       ├── api.js            # fetch wrapper
│   │       ├── chart_kline.js    # Chart.js K線
│   │       ├── chart_score.js    # 雷達圖 / 熱圖
│   │       └── strategy_race.js  # 策略競賽圖
│   └── templates/
│       ├── base.html
│       ├── market.html           # 行情總覽
│       ├── stock_detail.html     # 個股詳情 + 分數
│       ├── strategies.html       # 策略帳戶管理
│       └── competition.html      # 月度策略競賽
├── scripts/
│   ├── init_db.py                # 初始化資料庫
│   ├── backfill_year.py          # 補抓近一年歷史資料
│   └── run_scheduler.py          # 啟動排程背景程序
├── config/
│   ├── settings.py               # 所有設定（可 .env 覆蓋）
│   └── stock_universe.py         # 股票池定義
├── tests/
├── main.py                       # FastAPI app 入口
├── requirements.txt
└── Makefile                      # 一鍵指令
```

---

## 資料流設計

```
TWSE/MOPS Open Data
        │
        ▼
  collectors/         每日 21:00
  ├─ daily_eod        → SQLite: ohlcv_daily
  ├─ fundamental      → SQLite: fundamental_cache  (月更)
  ├─ chips            → SQLite: chips_daily
  └─ news_events      → SQLite: news_events

  intraday_tick       盤中 09:00–13:30 每分鐘
                      → SQLite: ohlcv_1min

        │
        ▼
  signals/            每日收盤後計算
  ├─ fundamental_score
  ├─ chip_score
  ├─ momentum_score
  ├─ macro_score
  └─ news_score
  → SQLite: daily_scores (stock_id, date, f_score, c_score, m_score, macro, news, composite)

        │
        ▼
  engine/
  ├─ scheduler        APScheduler 驅動所有排程
  ├─ paper_account    每個策略獨立帳戶 (初始 100萬)
  └─ order_executor   按策略信號模擬進出場

        │
        ▼
  FastAPI /api/*      JSON API
        │
        ▼
  Frontend            Chart.js + Tailwind
```

---

## 分數體系

| 分數類別 | 來源 | 更新頻率 |
|----------|------|----------|
| 基本面 (F) | 財報 EPS/ROE/負債比/月營收年增率 | 季 / 月 |
| 估值 (V) | PE/PB/PS 相對歷史百分位 | 日 |
| 籌碼 (C) | 三大法人買超/融資餘額/借券 | 日 |
| 量價動能 (M) | 成交量/內外盤比/VWAP 偏離 | 日/分鐘 |
| 總經 (Macro) | 台指期 PCR / 費城半導體 / SOXX / SPY | 日 |
| 新聞事件 (N) | 標題關鍵字情緒分 / 法說會日期接近度 | 日 |
| **綜合 (Composite)** | 加權平均，各策略可自訂權重 | 日 |

分數範圍：0–100，50 為中性

---

## 策略帳戶系統

每個策略帳戶：
- 初始資金：NT$1,000,000
- 獨立持股、現金、交易紀錄
- 自定義進場邏輯（規則 or ML）
- 自定義出場邏輯（停損停利 or 訊號反轉）
- 倉位管理（固定張數 / 固定金額 / Kelly）

月度競賽：比較各帳戶 30 天後總資產 + 夏普比率 + 最大回撤

---

## 快速啟動

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 初始化資料庫
python scripts/init_db.py

# 3. 補抓近一年歷史資料（約需 10–20 分鐘）
python scripts/backfill_year.py

# 4. 啟動排程背景程序
python scripts/run_scheduler.py &

# 5. 啟動 FastAPI
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 開啟瀏覽器
open http://localhost:8000
```

```bash
# Makefile 快捷指令
make init      # 初始化 DB
make backfill  # 補歷史資料
make dev       # 啟動開發模式（排程 + FastAPI）
make test      # 跑測試
```
