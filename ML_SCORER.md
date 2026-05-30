# ML_SCORER — 把 ML 變成主選股訊號（取代 v8_ml_scoring.py）

評估台已經證明 ML（Rank IC 0.059、t 6.39）的選股力是手刻 `final_score`（0.011）的三倍多。
`ml_scorer.py` 就是把這個驗證過的乾淨邏輯，做成「真正會產出每日選股排名」的正式版本。

它跟舊的 `scripts/v8_ml_scoring.py` 差在哪：

| | 舊 v8_ml_scoring.py | 新 ml_scorer.py |
|---|---|---|
| 股票池 | 只看規則篩過的 BUY/WATCH（選股偏誤） | 全市場（剔僵屍列+流動性過濾） |
| 防洩漏 | TimeSeriesSplit 按列切，無 embargo | 日期層級 walk-forward + embargo |
| ml_score | `pred*10+50` 再 clip（破壞排序） | 當天橫斷面百分位 0~100（保留排序） |
| 模型 | RandomForest | LightGBM（自動 fallback） |
| 寫入 | ml_score_results | 同一張表（前端零修改） |

## 怎麼用

**第一次（建議）— full 模式：** 跑整段歷史的 walk-forward + 最新日期，把 `ml_score_results` 一次填滿。每一天的分數都是無洩漏的樣本外預測，所以歷史分數也可信、可拿來回測。

```bash
cd twse_ml_eval
python3 ml_scorer.py --db ../data/db/quant.db --mode full
```

（LightGBM 下大約幾分鐘。）

**每天 — latest 模式：** 每天收盤、`daily_eod` 收完當天資料後跑這個，只訓練+評最新 1 天，很快。

```bash
python3 ml_scorer.py --db ../data/db/quant.db --mode latest --score-days 1
```

跑完，分數就寫進 `ml_score_results`，你現成的 `/api/v8/ml-scores` 會自動讀到，前端不用改任何東西。

## 怎麼讀分數

- `ml_score`：當天橫斷面百分位，**90 以上 = 模型當天最看好的前 10%**。這是要用的主訊號。
- `ml_rank`：當天排名，1 = 最佳。
- `predicted_return_5d`：模型預測的未來 5 日報酬（%），僅供參考透明度。

⚠️ **看排名，不要太相信 predicted_return_5d 的絕對值。** 模型擅長的是「誰比誰好」的排序（這就是 IC 在量的東西），不是精準預測報酬數字。選股請用 `ml_score` / `ml_rank`。

## 把它變成「主訊號」（整合步驟，在你的引擎裡做）

scorer 只負責產生乾淨的 ML 分數、寫進 `ml_score_results`，不去動你的 `final_score` 或選股引擎。
要讓 ML 變主訊號，在你的候選股／策略邏輯裡 join 這張表、用 `ml_rank` 排序即可。例如選每天 ML 前 20 名：

```sql
SELECT m.code, m.stock_name, m.ml_score, m.ml_rank
FROM ml_score_results m
WHERE m.score_date = (SELECT MAX(score_date) FROM ml_score_results)
ORDER BY m.ml_rank
LIMIT 20;
```

建議的漸進做法：先讓 ML 分數跟 `final_score` 並排顯示在前端，觀察一兩週實際選出來的股票順不順眼、跟你紙上帳戶的表現對不對得起來，再逐步把選股權重從 `final_score` 移到 `ml_rank`。

## 重訓頻率

每天 latest 模式跑一次就夠（模型會用到當天為止所有已知標籤資料）。
每隔一兩個月跑一次 full 模式，刷新整段歷史分數、順便讓模型吃進新的市場 regime。

## 注意：訊號吃 regime

評估台顯示各折 IC 落差大（fold3 幾乎 0，fold1/4 很高）。代表這個 ML 訊號不是每段行情都靈，
某些 regime 下會失效。實盤一定要配風控與部位控制，別把單一訊號當成穩賺。
