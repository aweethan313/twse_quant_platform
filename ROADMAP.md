# TWSE 量化平台 — 待辦 Roadmap

這份清單是整理自診斷過程中發現的所有問題，照「修的順序」排：地基（資料）先修，越上層越要等下層穩了再動。

狀態標記：`[x]` 已完成、`[~]` 進行中、`[ ]` 待辦。

---

## 第一層：資料品質（地基，下面全部都靠它）

- [x] **#1 `ohlcv_daily` 僵屍列**（~79% 是 stale/帶上來的重複資料）— 已用 `repair_stale_ohlcv_from_stock_day.py` 修復 2025 年起的部分。
- [ ] **#2 歷史資料 2017–2024 也可能髒** — 這次只修 2025+。若要把 ML 訓練往前延伸，這段要再修一次。
- [ ] **#3 `trading_calendar` 被污染** — 它是從髒掉的 `ohlcv_daily` 反推的，把假日（如 2025-01-01）標成開市。改用官方交易日曆重建。
- [ ] **#4 假日壞報價**（如 2330 在 2025-01-01 出現異常價）— 交易日曆修好後會一起解掉。

> 修完 #1 後第一件事：重算技術指標 + 重跑評估台，確認僵屍列佔比大幅下降、IC 變可信。詳見 RUNBOOK.md。

## 第二層：評分／訊號邏輯

- [ ] **#5 `valuation_score` 反向預測**（修復前 IC −0.084、t −2.40，統計顯著負貢獻）— 最高 CP 值。先跑 `experiment_valuation.py` 在修復後資料上確認，再把它從 `final_score` 拿掉或調降權重。
- [ ] **#6 `final_score` 綜合分數沒有預測力** — 好壞因子平均互相抵銷。依各 component 的 IC 重新加權（或直接用 ML 輸出取代手刻綜合分數）。
- [ ] **#7 `macro_score` 正確使用** — 它每天對所有股票都一樣，是「擇時」訊號不是「選股」訊號。確認在策略裡用對位置。
- [ ] **#8 `candidate_forward_returns` 只有 1418 筆**（被 BUY/WATCH 濾過）— 選股偏誤。在全市場重建。

## 第三層：ML pipeline（`v8_ml_scoring.py`）

- [x] **#9 walk-forward 標籤洩漏**（按列切、無 embargo）— 評估台已用「日期層級＋embargo」修正。`v8_ml_scoring.py` 本身還要把同樣邏輯搬回去。
- [x] **#10 用 MAE 評估** — 評估台改成 Rank IC + 分層回測。
- [x] **#11 沒有 baseline 對照** — 評估台已加上跟 `final_score` 並排比較。
- [x] **#12 只在規則篩過的候選股上訓練** — 評估台改成全市場。
- [~] **#13 模型名實不符** — 已建 `ml_scorer.py`，用 LightGBM、乾淨 walk-forward。可考慮再進一步換成 LightGBM ranking 目標。
- [ ] **#14 訓練資料太薄**（只有 2025+、單一 regime）— 資料修乾淨後往前延伸。
- [ ] **#15 `ml_score` 縮放後 clip / 未使用的 `StandardScaler` import** 等小髒 — 順手清。

## 第四層：架構與技術債

- [ ] **#16 patch 疊 patch**（`v3_api_patch`…`v8_patch`、`fix1`、`p0_fix`）— 合併回正式模組。
- [ ] **#17 `main.py` 136KB 巨石** — 拆檔。
- [ ] **#18 60 張表、多處重疊**（`trade_logs`／`paper_fills`／`realistic_trade_fills` 三套成交表）— 整併 schema。
- [ ] **#19 測試只有一支** `test_v6_core.py` — 核心（撮合、評分、no-lookahead）至少要有單元測試。
- [~] **#20 ML 正式化** — 已用 `ml_scorer.py` 取代 v8_ml_scoring，寫進 ml_score_results、前端 /api/v8/ml-scores 自動接上。下一步：在選股引擎用 ml_rank 當主訊號（見 ML_SCORER.md）。

## 第五層：前端（排在資料/訊號之後）

- [ ] **#21 `.bak` 檔大清理** — `templates/` 和 `scripts/` 裡一堆 `.bak`、patch 檔，純檔案刪除，避免改錯檔。
- [ ] **#22 資料品質指標要說真話** — `v4_dq_score` 沒反映出 staleness。把評估台的僵屍列偵測接進去。
- [ ] **#23 顯示「樣本外」績效而非美化數字** — 績效/競賽頁標清楚 in-sample vs out-of-sample，並扣交易成本。
- [ ] **#24 設計層面整理**（emoji 過多、資訊密度、一致性）— 純美化，最低優先。

## 部署（個人用）

- [x] **Tailscale 私人網路** — Mac 端已裝好（IP 100.127.36.22）。
- [ ] **手機端確認** — 手機裝 Tailscale、同帳號登入、裝置清單看得到 Mac。
- [ ] **Part B：啟動 app + 手機開啟** `http://100.127.36.22:8000`（詳見 RUNBOOK.md）。

---

## 優先順序建議

修完 #1（已完成）→ 重算技術指標 + 重跑評估台確認 IC 變可信 → 跑 `experiment_valuation.py` 處理 #5（改一行就可能讓 final_score 轉正）→ #6 重新加權 → 第四層技術債（#16–20）不急，但在把 ML 正式接進策略前最好先還一輪。前端（第五層）等訊號層穩了再做。
