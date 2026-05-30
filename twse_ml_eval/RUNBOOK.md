# RUNBOOK — 資料修復後的執行順序

資料修復（#1）已完成。照這個順序做，每一步都先確認結果再進下一步。
所有指令在**專案根目錄** `twse_quant_platform 第一版 3/` 執行（除非另外標明），記得先 `source .venv/bin/activate`。

---

## 步驟 1：重算技術指標（必須先做）

技術指標（rsi14、distance_ma20、return_5d 等）是用 `ohlcv_daily` 算的，而 ohlcv 剛修好，
所以舊的技術指標還是基於髒資料。先重算，後面的評估才會用到正確的特徵。

```bash
python3 -m scripts.build_technical_daily_features --all
```

## 步驟 2：重跑評估台，確認修復生效

```bash
cd twse_ml_eval
python3 run_eval.py --db ../data/db/quant.db
```

**要看的重點：**
- 「資料品質」那段的**僵屍列佔比應該從 ~79% 大幅下降**。如果還是很高，代表修復沒吃到，回頭檢查。
- 樣本數應該明顯變多（之前只有 ~4.8 萬列）。
- ML 的 IC 和 t 值：跟修復前（IC 0.066 / t 5.26）比，**這次才是真正可信的基準**。
  數字可能往上也可能往下——往下不代表失敗，代表之前的高 IC 有一部分是髒資料造成的假象。

## 步驟 3：處理 valuation_score（#5，CP 值最高）

```bash
# 在 twse_ml_eval 資料夾裡
python3 experiment_valuation.py --db ../data/db/quant.db
```

它會列出每個 component score 的 IC、找出拖後腿的因子，並比較「丟掉壞因子的等權組合」vs 你的 `final_score`。

**根據結論行動：**
- 如果 `valuation_score` 仍是強烈負 IC → 到你的評分模組（`backend/signals/scorer*.py`），
  把它從 `final_score` 的計算式移除或調降權重，重新計算分數。
- 如果「正IC組合」明顯贏過 final_score → 考慮按各因子 IC 重新加權（#6）。

## 步驟 4：（可選）用 v2 的籌碼趨勢特徵再跑一次

v2 評估台多了 6 個籌碼趨勢特徵（3/5 日累計買超、連續買超天數）。比較有沒有讓 IC 上升：

```bash
python3 run_eval.py --db ../data/db/quant.db   # v2 會自動載入 35 特徵
```

特徵數顯示 35（而非 29）就代表籌碼趨勢有進去。

---

## 部署 Part B：從手機開啟你的儀表板

確認手機已裝 Tailscale 並用同帳號登入後：

```bash
# Mac 上，專案根目錄
source .venv/bin/activate
caffeinate -s uvicorn main:app --host 0.0.0.0 --port 8000
```

（`caffeinate -s` 讓 Mac 在 app 跑的時候不睡眠。）

手機（Tailscale 開著）瀏覽器輸入：

```
http://100.127.36.22:8000
```

注意是 `http://` 不是 `https://`。Mac 醒著、app 在跑、兩台 Tailscale 都 ON 才連得到。

---

## 接下來

做完上面，回 ROADMAP.md 把對應項目打勾，然後依優先順序往下：#6 重新加權 → 第四層技術債 → 前端。
任何一步卡住或數字看不懂，把終端機輸出貼出來討論。
