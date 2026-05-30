# TWSE 日線 ML 誠實評估台

一個獨立的小工具，回答你最該知道、但 `v8_ml_scoring.py` 從來沒回答的三個問題：

1. **你的訊號到底有沒有「樣本外」的預測力？**（Rank IC）
2. **ML 有沒有贏過直接用 `final_score` 排序？**（對照基準）
3. **扣掉台股交易成本後，Top 層還賺不賺？**（分層回測淨報酬）

它直接讀你的 `quant.db`，全程用「日期層級 + embargo」的 purged walk-forward，
杜絕原本 `TimeSeriesSplit` 按列切造成的標籤洩漏。

---

## 為什麼需要它（修正了 v8_ml_scoring.py 的問題）

| 原本的問題 | 這裡的修正 |
|---|---|
| `TimeSeriesSplit` 按「列」切，一天 ~200 檔，gap 沒隔到日子 → 標籤洩漏 | 按「交易日」切，train 末端挖掉 `embargo`（= 標籤天數）個交易日 |
| 用 MAE 評估 → 完全看不出有沒有 edge | 用 Rank IC + 分層回測 + 命中率 |
| 沒有 baseline → 不知道 ML 有沒有比規則分數好 | 每個指標都同時算 ML 與 `final_score` 並排比較 |
| 訓練/標籤只在 BUY/WATCH 候選股上 → 選股偏誤 | 在全市場（流動性過濾）上自算前瞻報酬 |
| 直接回歸原始 5 日報酬 | 一樣回歸，但用排序指標評估（模型可換 LightGBM ranker） |
| 無交易成本 | 內建台股成本：手續費 0.1425%×2 + 證交稅 0.3% = 來回 0.585% |

---

## 安裝與執行

把整個 `twse_ml_eval/` 資料夾放到你的專案根目錄（跟 `data/` 同層），然後：

```bash
pip install -r twse_ml_eval/requirements.txt   # 多數你已裝過
cd twse_ml_eval
python3 run_eval.py                              # 用預設：data/db/quant.db, 5日, 5折
```

常用參數：

```bash
python3 run_eval.py --db ../data/db/quant.db --horizon 5 --n-splits 5
python3 run_eval.py --model lgbm                 # 強制用 LightGBM（有裝的話）
python3 run_eval.py --model rf                   # 強制用 RandomForest
python3 run_eval.py --start 2025-03-01 --end 2026-05-01
python3 run_eval.py --horizon 10 --embargo 10    # 換成 10 日持有
```

輸出會印在終端機，同時寫到 `data/ml_eval/`：`results.json`、`fold_metrics.csv`、`summary.md`。

---

## 怎麼讀結果

- **Rank IC 平均**：每天「預測排名 vs 實際前瞻報酬排名」的 Spearman 相關，跨日平均。
  - ≤ 0：沒有預測力。 0.02~0.03：勉強可用。 > 0.05：不錯。
- **IC t 值**：> 2 才算統計上可信；< 2 很可能是運氣。
- **各折 IC**：看穩不穩。如果有的折正、有的折負，代表訊號吃 regime，不夠 robust。
- **分層報酬輪廓**：理想是「層數越高、報酬越高」單調遞增。亂跳代表沒有真訊號。
- **Top 層淨報酬**：扣成本後 > 0 才有意義。很多策略毛報酬看起來不錯，扣 0.585% 就變負。
- **ML vs final_score**：ML 要明顯贏過基準，這層 ML 才算有加分；打平或輸 = 不如直接用規則分數。

---

## ⚠️ 重要：你的 `ohlcv_daily` 有資料品質問題

跑這個工具時第一個會跳出來的，是一段「資料品質」統計。在目前的 `quant.db` 上，
**約 80% 的個股交易日是「僵屍列」**——收盤價與成交金額和前一天完全相同，
是每日收盤收集失敗或修復時被「整列帶上來」的資料（你已經有 `diagnose_stale_ohlcv.py`、
`repair_stale_ohlcv_from_stock_day.py` 在處理這件事）。

本工具用「當天 close 或 value 與前一列不同」當作「真的有成交」的判斷，把僵屍列剔除後再評估，
所以結果是可信的。**但這代表你資料源該優先修**：在髒資料上跑出來的任何回測、任何 ML 分數，
都不能全信。修好資料源，這裡的樣本數會大增，IC 的 t 值也會更有意義。

另外，從 `ohlcv_daily` 反推的 `trading_calendar` 也繼承了髒資料（例如把假日 2025-01-01 標成開市），
建議改用官方交易日曆校正。

---

## 結構

```
twse_ml_eval/
├── run_eval.py            # CLI 入口：串起資料→walk-forward→模型→指標→報告
├── requirements.txt
├── README.md
└── ml_eval/
    ├── config.py          # 所有預設值、特徵清單、台股交易成本（改這裡就好）
    ├── data.py            # 組特徵 + 自算前瞻報酬 + 資料品質清洗
    ├── walkforward.py     # 日期層級 + embargo 的 purged walk-forward 切割器
    ├── models.py          # 模型工廠：LightGBM 優先，否則 sklearn
    ├── metrics.py         # Rank IC、分層回測、交易成本
    └── report.py          # 終端報告 + json/csv/md 輸出 + 白話結論
```

## 下一步建議

1. **先修 `ohlcv_daily` 的僵屍資料**，再回來跑，看 IC 的 t 值會不會變得可信。
2. 如果 ML 穩定贏過 `final_score`，把特徵擴充（籌碼趨勢、估值百分位、月營收年增），再跑一次看 IC 有沒有上升。
3. 把標籤從「絕對報酬」換成「相對 0050 的超額報酬」(`config.py` 可擴充)，通常更穩定。
4. 確認 ML 真的有 edge 後，再把它正式收編成 `backend/strategies/ml_based/` 的一個策略類別，接進你的 paper account。
