"""驗證:A7 的問題是模型還是執行方式(TopN × 持有期)"""
import statistics as st
from sqlalchemy import text
from backend.models.database import SessionLocal

VER = "lgbm_exp_h5"
COMBOS = [(5, 5), (5, 20), (5, 60), (20, 5), (20, 20), (20, 60)]

db = SessionLocal()
days = [str(r[0]) for r in db.execute(text("""
    SELECT DISTINCT trade_date FROM ohlcv_daily
    WHERE trade_date >= '2024-10-01' AND code GLOB '[0-9][0-9][0-9][0-9]'
    ORDER BY trade_date""")).fetchall()]

print(f"評估區間 {days[0]} ~ {days[-1]},模型 {VER}\n")
print("="*72)
print(f"{'持股數':>8}{'持有期':>8}{'選股報酬':>12}{'市場平均':>12}{'超額':>10}{'贏市場':>10}{'樣本':>8}")
print("-"*72)

for topn, hold in COMBOS:
    rets, mkts = [], []
    step = max(hold, 5)
    for i in range(0, len(days) - hold, step):
        d, fd = days[i], days[i + hold]
        rows = db.execute(text("""
            SELECT (f.close/o.close - 1)*100
            FROM ml_score_experiments e
            JOIN ohlcv_daily o ON o.code=e.code AND o.trade_date=:d
            JOIN ohlcv_daily f ON f.code=e.code AND f.trade_date=:fd
            WHERE e.score_date=:d AND e.model_version=:v AND e.ml_rank <= :n
              AND o.close > 10 AND f.close > 0
        """), {"d": d, "fd": fd, "v": VER, "n": topn}).fetchall()
        if len(rows) < max(3, topn // 2):
            continue
        rets.append(st.mean([float(r[0]) for r in rows]))
        m = db.execute(text("""
            SELECT AVG((f.close/o.close - 1)*100)
            FROM ohlcv_daily o JOIN ohlcv_daily f ON f.code=o.code AND f.trade_date=:fd
            WHERE o.trade_date=:d AND o.code GLOB '[0-9][0-9][0-9][0-9]'
              AND o.close > 10 AND f.close > 0
        """), {"d": d, "fd": fd}).scalar()
        mkts.append(float(m or 0))
    if rets:
        r, m = st.mean(rets), st.mean(mkts)
        win = sum(1 for a, b in zip(rets, mkts) if a > b) / len(rets) * 100
        mark = "  ← A7 現行" if (topn, hold) == (5, 5) else ""
        print(f"{topn:>8}{hold:>6}日{r:>11.2f}%{m:>11.2f}%{r-m:>+9.2f}%{win:>9.0f}%{len(rets):>8}{mark}")

db.close()
print("="*72)
print("\n判讀:若 Top5 短持有明顯較差,證實問題在執行方式而非模型")
