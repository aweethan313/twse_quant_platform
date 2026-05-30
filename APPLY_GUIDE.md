# 套用說明 — 高 CP 小修包

把這包的檔案放到專案根目錄對應位置即可。動手前建議先存 git 快照：

```bash
cd "/Users/yangyichen/Downloads/twse_quant_platform 第一版 3"
git add -A && git commit -m "套用小修包之前的快照"
```

---

## 1. 清理（cleanup.py）— 第五層 #1

把 `cleanup.py` 放到專案根目錄，先預覽再執行：

```bash
python3 cleanup.py            # 預覽
python3 cleanup.py --apply    # 執行：刪 {backend、搬備份模板、移除重複路由
```

## 2. /v8 ML 顏色閾值 65→90 — 第五層 #3

直接用本包的 `frontend/templates/v8_overview.html` 覆蓋同名檔即可（只改了一行門檻）。
百分位資料下，現在只有「當天最看好的前 10%」會標紅、最差 10% 標綠，紅色才有意義。

## 3. ML 選股頁 + 把 ml_rank 變成第一級選股工具 — 第三層 #5 / #20

這是把驗證過的 ML edge 真正接進選股流程。採「加值、不動既有引擎」的安全做法：

放入這些檔案（都在本包對應路徑）：
- `backend/api_extensions.py`（新檔）— 新增 `/api/ml-picks`、`/api/data-health/staleness`、`/ml-picks` 三個端點
- `frontend/templates/ml_picks.html`（新檔）— ML 選股頁
- `frontend/templates/base.html`（覆蓋）— 導覽列多了「ML 選股」連結

然後在 `main.py` **加兩行**（在第 37 行 `templates = Jinja2Templates(...)` 之後）：

```python
from backend.api_extensions import register_extensions
register_extensions(app, templates)
```

完成後開 `http://localhost:8000/ml-picks`：會看到當日 ML Top 20，並標出哪些「規則引擎也看好」（ML ∩ 規則 = 信心更高）。這頁不碰你原本的 trade-plan 引擎，零風險。

> 之後若要更深的整合（讓 ml_rank 直接參與 trade-plan 的候選生成），那是動 `backend/v3/candidate_trade_plans.py` 的較大工程，建議確認這頁的選股實際好用後再做。

## 4. data-health 頁接上真實僵屍列偵測 — 第五層 #2

用本包的 `frontend/templates/data_health.html` 覆蓋同名檔。它會呼叫上面第 3 步加的
`/api/data-health/staleness`，在頁面顯示「近 60 天、四位數普通股中 close+value 與前一日完全相同的比例」。
你修復後的資料現在約 4.9%（健康）。**所以這步依賴第 3 步的 `register_extensions` 已掛上。**

---

## 5.（可選、需你判斷）從 final_score 移除毒因子 — 第二層 #1

**這項我刻意沒有自動幫你改**，因為它不是單純的 bug，是一個權衡判斷：

你的公式是 `final_score = candidate*0.6 + entry*0.4 − risk懲罰`，其中
`risk懲罰 = max(0, risk_score−40) * 0.5`。

我們發現 `risk_score` 在這段期間反向預測（高風險股反而漲），但那是因為 2025 是多頭、
高風險/高 beta 股表現好——這是 **regime 現象**，不代表「風控扣分」這件事本身錯。
把懲罰拿掉會讓分數變得追逐高風險股，多頭時看起來變好、一旦轉空可能更慘。

而且 `valuation_score`、`volume_score` 是包在 `candidate_score` 裡面，不在這條公式直接相加，
要動得進 candidate_score 的計算，較深。

**我的建議：既然 ML 已是主訊號、final_score 退居輔助，這項的價值不高，可以先不動。**
如果你仍想試「拿掉風險懲罰」，只改 `backend/signals/scorer_v2.py` 的 `compute_final_score`：

```python
# 原本
def compute_final_score(candidate: float, entry: float, risk: float) -> float:
    penalty = max(0.0, risk - 40) * 0.5
    return round(max(0, min(100, candidate * 0.6 + entry * 0.4 - penalty)), 2)

# 若要拿掉風險懲罰（請自行斟酌上述權衡）
def compute_final_score(candidate: float, entry: float, risk: float) -> float:
    return round(max(0, min(100, candidate * 0.6 + entry * 0.4)), 2)
```

改完要重新計算歷史 daily_scores 才會生效（這是大動作），所以更不建議現在做。

---

## 套用後驗收

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

- `/ml-picks` → 看到當日 ML Top 20 + 規則交集標記
- `/data-health` → 看到「🧟 僵屍列偵測」卡片，顯示約 4.9% 健康
- `/v8` → ML 分數只有前/後 10% 才上色

任一頁壞掉就 `git restore` 還原，把錯誤訊息貼給我。
