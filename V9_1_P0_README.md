# V9.1-P0 Daily-only 修正版

這包已經套用 P0 修正，範圍維持：

- 上市股票 + ETF 為主
- 日級資料 / 日 K 為主
- 不加入 `ohlcv_1min` 分鐘資料表
- 不要求 TPEx 上櫃全市場

## 已修正內容

1. 新增日級交易日守門員：`backend/utils/trading_day.py`
2. `daily_eod.run_eod()` 遇到週末 / `trading_calendar.is_open=0` 會直接跳過，不再寫假日日 K。
3. `TWSEClient.fetch_daily_all()` 加入回傳日期檢查，避免休市日抓到前一交易日資料卻寫成休市日。
4. 新增 `scripts/v9_1_p0_fix.py`：
   - 建立 / 修正 `trading_calendar`
   - 清除週末與已知 TWSE 休市日污染資料
   - 重建 0050 benchmark
   - 產生 `data/reports/v9_1_p0_fix_report.md`
5. `/api/ml-picks` 與 `/api/v8/ml-scores` 預設只讀 `lgbm_v9_clean`，避免被舊 `v8_rf_v1` 最新日期覆蓋。
6. `MLTop5` 策略只吃 `lgbm_v9_clean`。
7. `twse_ml_eval/ml_eval/data.py` 修正 train / predict 切分：最新幾天沒有 `fwd_ret` 標籤時仍可被預測，訓練仍只用已知標籤資料，避免偷看未來。
8. `run_daily_update.sh` 改成用腳本所在目錄當專案根目錄，降低 launchd 指到舊資料夾的機率。

## 已在這包 DB 跑過

```bash
python3 scripts/v9_1_p0_fix.py --apply
```

報告位置：

```text
data/reports/v9_1_p0_fix_report.md
```

## 之後你本機更新 ML

因為這次 zip 打包沒有在我的環境完整重跑 ML 訓練，回到你本機後建議跑：

```bash
cd "你的專案根目錄/twse_ml_eval"
python3 ml_scorer.py --db ../data/db/quant.db --mode latest --score-days 5
```

跑完後 `/ml-picks` 就會讀最新的 `lgbm_v9_clean`。
