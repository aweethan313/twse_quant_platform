"""測試不同預測期的 IC:訊號是否在更長週期才顯現"""
import statistics as st
from sqlalchemy import text
from backend.models.database import SessionLocal

HORIZONS = [5, 20, 60, 120]
db = SessionLocal()

days = [str(r[0]) for r in db.execute(text("""
    SELECT DISTINCT trade_date FROM ohlcv_daily
    WHERE trade_date >= '2023-01-01' AND code GLOB '[0-9][0-9][0-9][0-9]'
    ORDER BY trade_date""")).fetchall()]

def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda k: v[k])
        rk = [0]*len(v)
        for pos, idx in enumerate(order):
            rk[idx] = pos
        return rk
    rx, ry = rank(xs), rank(ys)
    n = len(rx)
    mx, my = sum(rx)/n, sum(ry)/n
    num = sum((rx[k]-mx)*(ry[k]-my) for k in range(n))
    den = (sum((rx[k]-mx)**2 for k in range(n)) * sum((ry[k]-my)**2 for k in range(n))) ** 0.5
    return num/den if den > 0 else 0

print(f"共 {len(days)} 個交易日\n測試預測期: {HORIZONS}\n")
results = {h: [] for h in HORIZONS}

# 每 5 天抽樣一次(降低計算量,足夠估計)
for i in range(0, len(days), 5):
    d = days[i]
    for h in HORIZONS:
        if i + h >= len(days):
            continue
        fd = days[i + h]
        rows = db.execute(text("""
            SELECT ds.final_score, (f.close / o.close - 1) * 100
            FROM daily_scores ds
            JOIN ohlcv_daily o ON o.code=ds.code AND o.trade_date=:d
            JOIN ohlcv_daily f ON f.code=ds.code AND f.trade_date=:fd
            WHERE ds.score_date=:d AND ds.final_score IS NOT NULL
              AND o.close > 10 AND f.close > 0 AND ds.code GLOB '[0-9][0-9][0-9][0-9]'
        """), {"d": d, "fd": fd}).fetchall()
        if len(rows) < 100:
            continue
        ic = spearman([float(r[0]) for r in rows], [float(r[1]) for r in rows])
        results[h].append(ic)
    if (i//5 + 1) % 30 == 0:
        print(f"  {i}/{len(days)}", flush=True)

db.close()

print("\n" + "="*66)
print("不同預測期的 IC(final_score vs 未來 N 日報酬)")
print("="*66)
print(f"{'預測期':<10}{'樣本數':>8}{'平均IC':>10}{'IC標準差':>11}{'ICIR':>9}{'正IC比例':>11}")
print("-"*66)
for h in HORIZONS:
    ics = results[h]
    if not ics:
        continue
    mu = st.mean(ics)
    sd = st.pstdev(ics) if len(ics) > 1 else 0
    icir = mu/sd if sd > 1e-9 else 0
    pos = sum(1 for x in ics if x > 0)/len(ics)*100
    print(f"{h:>3} 日{'':<5}{len(ics):>8}{mu:>10.4f}{sd:>11.4f}{icir:>9.2f}{pos:>10.1f}%")
print("="*66)
print("\n判讀:")
print("  若長週期 IC 明顯較高 → 訊號屬中長期,ML label(5日)設計不匹配")
print("  若各週期差不多 → 訊號強度本身是限制,非週期問題")
