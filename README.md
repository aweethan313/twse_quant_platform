# TWSE Alpha Lab — 台股量化研究平台

一個個人用的台股量化研究平台：每日自動收集台股資料、計算多維度評分、用經過嚴格驗證的
機器學習模型對全市場選股，並透過網頁介面呈現。資料、訊號、ML、前端、每日排程一條龍。

> 個人研究用途，非投資建議。不對外公開、不提供他人投資推介。

---

## 核心理念（這個專案踩過的坑換來的）

1. **資料乾淨優先**。再聰明的模型，建在髒資料上都是假的。本專案曾有約 80% 的個股日 K 是
   「僵屍列」（收盤價與成交額被整列複製），修復後才得到可信結果。
2. **杜絕未來函數（no-lookahead）**。所有特徵只用「當下已知」資料；標籤（未來報酬）只用未來價。
   驗證一律用「日期層級 + embargo」的 purged walk-forward，避免標籤洩漏造成的虛高績效。
3. **訊號要驗證、不能憑感覺**。任何選股訊號都要看樣本外的 Rank IC、分層回測、扣交易成本後的
   淨報酬，並跟基準（規則分數）並排比較——贏不過基準的訊號就是沒用。
4. **看排名，不信絕對預測值**。ML 擅長的是「誰比誰好」的排序，不是精準預測報酬數字。

---

## 快速開始

環境：Python 3.12、venv 位於 `../twse_quant_platform 第一版/.venv`、LightGBM 需要 `brew install libomp`。

```bash
# 啟動網頁伺服器
source "/Users/yangyichen/Downloads/twse_quant_platform 第一版/.venv/bin/activate"
uvicorn main:app --host 0.0.0.0 --port 8000
# 開 http://localhost:8000
```

手動跑一次完整每日流程（平常由排程自動執行）：

```bash
bash run_daily_update.sh        # 收盤資料 → 技術指標 → 評分 → ML 分數
```

更新 / 驗證 ML 分數：

```bash
cd twse_ml_eval
python3 ml_scorer.py --db ../data/db/quant.db --mode latest    # 每日：評最新一天
python3 ml_scorer.py --db ../data/db/quant.db --mode full      # 重建整段歷史分數
python3 run_eval.py  --db ../data/db/quant.db                  # 驗證訊號（IC / 分層 / 對照）
```

---

## 系統架構（資料流）

```
TWSE API ─► 收集器(collectors) ─► quant.db (SQLite)
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                          ▼
   技術指標(technical_           規則評分(scorer_v2)        ML 評分(ml_scorer)
   daily_features)              → daily_scores            → ml_score_results
            └─────────────┬───────────┴──────────────┬──────────┘
                          ▼                           ▼
                    FastAPI (main.py + api_extensions.py)
                          ▼
                    前端頁面 (Jinja2 + Tailwind)
```

- **資料層**：`ohlcv_daily`（日 K）、`chip_daily`（籌碼）、`institutional`（法人）、`technical_daily_features`（技術指標）。
- **訊號層**：
  - 規則分數 `final_score`（= candidate×0.6 + entry×0.4 − 風險懲罰），存於 `daily_scores`。
  - **ML 分數（主訊號）**：`ml_scorer.py` 產出，存於 `ml_score_results`。
- **呈現層**：FastAPI 提供 API，Jinja2 模板渲染頁面。

---

## 主要頁面

| 路徑 | 名稱 | 用途 |
|------|------|------|
| `/v3` | 每日作戰室 | 當日總覽 |
| `/` | 市場總覽 | 大盤與市場概況 |
| `/candidates` | 今日選股 | 規則引擎候選股 |
| `/ml-picks` | **ML 選股** | ML Top 20，標出規則也看好的交集 |
| `/paper` | 模擬交易 | 紙上帳戶 |
| `/competition` | 策略排行榜 | 多策略競賽 |
| `/lab` | 研究室 | 回測、因子、產業輪動等研究工具 |
| `/data-health` | 資料健康 | 含真實僵屍列偵測 |

---

## ML 訊號（這個平台的核心差異化）

`twse_ml_eval/` 是整套乾淨的 ML 流程：

- `run_eval.py`：誠實評估台。日期層級 + embargo walk-forward、Rank IC、分層回測、扣台股交易成本（來回 0.585%）、與 `final_score` 並排對照。
- `ml_scorer.py`：正式選股評分。全市場、LightGBM、無洩漏，寫進 `ml_score_results`。
- `experiment_valuation.py`：診斷各 component score 的預測力。

**最近一次驗證結果（修復後的乾淨資料）：**

| 指標 | ML | 規則 final_score |
|------|------|------|
| 樣本外 Rank IC | **0.059** | 0.011 |
| IC t 值 | **6.39** | 1.76 |
| Top 層扣成本淨報酬（每 5 日） | +1.28% | +0.10% |

ML 的選股力約是手刻規則分數的 **3 倍**，且分層報酬單調遞增（越高分報酬越高）。
診斷也發現 `valuation_score`、`risk_score`、`volume_score` 反向預測（拖累 final_score）。

> ⚠️ 訊號吃市場 regime（各折 IC 落差大，某些行情會失效），實盤務必配合風控與部位管理。

---

## 每日自動更新

由 macOS launchd 每個交易日 16:30 觸發 `run_daily_update.sh`（週末/假日自動跳過）：

```
收盤資料(run_eod) → 技術指標 → 評分流程 → ML 分數更新
```

```bash
launchctl list | grep twse                       # 確認排程已載入
launchctl start com.twse.daily_update            # 立刻手動觸發一次
tail -50 data/logs/daily_update.log              # 看執行 log
```

---

## 專案結構

```
twse_quant_platform 第一版 3/
├── main.py                     # FastAPI 主程式（網頁 + API）
├── run_daily_update.sh         # 每日自動更新腳本（launchd 觸發）
├── config/settings.py          # 設定
├── backend/
│   ├── collectors/             # 資料收集（daily_eod 等）
│   ├── signals/                # 評分（scorer_v2 → final_score）
│   ├── services/               # 技術指標等服務
│   ├── strategies/ engine/     # 策略與撮合
│   ├── v3/ v4/ v5/ v6/         # 各階段功能模組
│   └── api_extensions.py       # 加值 API（ML 選股、僵屍列偵測）
├── frontend/
│   ├── templates/              # Jinja2 頁面
│   └── static/                 # CSS / JS
├── scripts/                    # 日常 / 修復 / 建表腳本
│   ├── repair_stale_ohlcv_from_stock_day.py   # 修僵屍列
│   ├── build_technical_daily_features.py      # 算技術指標
│   └── v4_3_run_daily_workflow.py             # 評分流程
├── twse_ml_eval/               # ML 評估台 + 正式評分器
│   ├── run_eval.py
│   ├── ml_scorer.py
│   ├── experiment_valuation.py
│   └── ml_eval/                # 共用模組（資料清洗、walk-forward、指標）
├── data/db/quant.db            # SQLite 資料庫
├── data/logs/                  # 執行 log
└── tests/                      # 測試（目前覆蓋仍薄）
```

---

## 已知限制 / 注意事項

- 資料庫是 SQLite，適合單人本地用；多人或高頻會撐不住。
- 歷史資料只修復到 2025 年起；2017–2024 仍可能含僵屍列（若要把 ML 訓練往前延伸需先修）。
- `trading_calendar` 從 `ohlcv_daily` 反推，可能把假日標成開市。
- ML 訓練資料僅約 1.4 年（單一市場 regime），跨 regime 穩定性尚待驗證。
- 詳細待辦見下方「尚未完成」。

---

## 尚未完成（Roadmap）

依價值排序：

**短期 / 高價值**
- [ ] 觀察驗證：讓排程跑一兩週，用紙上帳戶追蹤 `/ml-picks` 的實際表現
- [ ] 把 `ml_rank` 深度整合進 `backend/v3/candidate_trade_plans.py` 的候選生成（目前 ML 是獨立頁，尚未驅動 trade-plan 引擎）

**中期**
- [ ] 補單元測試：撮合、評分、no-lookahead 等核心（目前幾乎只有 `test_v6_core.py`）
- [ ]（可選）從 `final_score` 移除風險懲罰／毒因子——屬權衡判斷，ML 已是主訊號故優先度低
- [ ] Tailscale 手機端驗證（Mac 端已設好）

**長期 / 地基**
- [ ] 修復 2017–2024 歷史資料 + 用官方交易日曆重建 `trading_calendar`
- [ ] 之後把 ML 訓練資料往前延伸到多個市場 regime
- [ ] 後端技術債：合併 patch 檔、拆分 3000+ 行的 `main.py`、整併重複資料表
- [ ] ML 模型可進一步改用 LightGBM ranking 目標（lambdarank）
```

