"""比較三個 horizon 的實際選股品質(不是模型自評,是真實報酬)"""
import statistics as st
from sqlalchemy import text
from backend.models.database import SessionLocal

VERSIONS = ["lgbm_exp_h5", "lgbm_exp_h60", "lgbm_exp_h120"]
TOPN = 20
HOLD_LIST = [20, 60]   # 用不同持有期評估

db = SessionLocal()
days = [str(r[0]) for r in db.execute(text("""
    SELECT DISTINCT trade_date FROM ohlcv_daily
    WHERE trade_date >= '2024-10-01' AND code GLOB '[0-9][0-9][0-9][0-9]'
    ORDER BY trade_date""")).fetchall()]

print(f"評估區間 {days[0]} ~ {days[-1]},共 {len(days)} 個交易日")
print(f"每次取 Top{TOPN},每 20 日抽樣一次\n")

results = {}
for ver in VERSIONS:
    for hold in HOLD_LIST:
        key = (ver, hold)
        rets, mkt_rets = [], []
        for i in range(0, len(days) - hold, 20):
            d, fd = days[i], days[i + hold]
            rows = db.execute(text("""
                SELECT e.code, (f.close/o.close - 1)*100 AS ret
                FROM ml_score_experiments e
                JOIN ohlcv_daily o ON o.code=e.code AND o.trade_date=:d
                JOIN ohlcv_daily f ON f.code=e.code AND f.trade_date=:fd
                WHERE e.score_date=:d AND e.model_version=:v AND e.ml_rank <= :n
                  AND o.close > 10 AND f.close > 0
            """), {"d": d, "fd": fd, "v": ver, "n": TOPN}).fetchall()
            if len(rows) < 5:
                continue
            rets.append(st.mean([float(r[1]) for r in rows]))
            m = db.execute(text("""
                SELECT AVG((f.close/o.close - 1)*100)
                FROM ohlcv_daily o JOIN ohlcv_daily f
                  ON f.code=o.code AND f.trade_date=:fd
                WHERE o.trade_date=:d AND o.code GLOB '[0-9][0-9][0-9][0-9]'
                  AND o.close > 10 AND f.close > 0
            """), {"d": d, "fd": fd}).scalar()
            mkt_rets.append(float(m or 0))
        if rets:
            results[key] = (st.mean(rets), st.mean(mkt_rets), len(rets),
                            sum(1 for a, b in zip(rets, mkt_rets) if a > b) / len(rets) * 100)

db.close()

print("="*76)
print(f"{'模型版本':<18}{'持有期':>8}{'選股報酬':>12}{'市場平均':>12}{'超額':>10}{'贏市場比例':>12}")
print("-"*76)
for (ver, hold), (r, m, n, win) in sorted(results.items(), key=lambda x: (x[0][1], x[0][0])):
    print(f"{ver:<18}{hold:>6}日{r:>11.2f}%{m:>11.2f}%{r-m:>+9.2f}%{win:>11.0f}%")
print("="*76)
print("\n判讀:超額為正且贏市場比例 > 60% 才算有實質選股能力")
