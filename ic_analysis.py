"""
分數預測力衰減分析(IC = Information Coefficient)
計算 final_score 與未來 20 日報酬的相關係數,分年觀察。
"""
import statistics as st
from sqlalchemy import text
from backend.models.database import SessionLocal

HORIZON = 20   # 未來 20 個交易日

db = SessionLocal()

# 取所有交易日
days = [str(r[0]) for r in db.execute(text("""
    SELECT DISTINCT trade_date FROM ohlcv_daily
    WHERE trade_date >= '2023-01-01' AND code GLOB '[0-9][0-9][0-9][0-9]'
    ORDER BY trade_date""")).fetchall()]

print(f"共 {len(days)} 個交易日,horizon={HORIZON} 日\n")

results = {}   # year -> [ic, ic, ...]

for i, d in enumerate(days):
    if i + HORIZON >= len(days):
        break
    future_d = days[i + HORIZON]
    yr = d[:4]

    rows = db.execute(text("""
        SELECT ds.final_score, (f.close / o.close - 1) * 100 AS fwd_ret
        FROM daily_scores ds
        JOIN ohlcv_daily o ON o.code=ds.code AND o.trade_date=:d
        JOIN ohlcv_daily f ON f.code=ds.code AND f.trade_date=:fd
        WHERE ds.score_date=:d AND ds.final_score IS NOT NULL
          AND o.close > 10 AND f.close > 0
          AND ds.code GLOB '[0-9][0-9][0-9][0-9]'
    """), {"d": d, "fd": future_d}).fetchall()

    if len(rows) < 100:
        continue

    scores = [float(r[0]) for r in rows]
    rets = [float(r[1]) for r in rows]

    # Spearman 等級相關(對極端值較穩健)
    def rank(xs):
        order = sorted(range(len(xs)), key=lambda k: xs[k])
        rk = [0]*len(xs)
        for pos, idx in enumerate(order):
            rk[idx] = pos
        return rk
    rs, rr = rank(scores), rank(rets)
    n = len(rs)
    mean_s, mean_r = sum(rs)/n, sum(rr)/n
    num = sum((rs[k]-mean_s)*(rr[k]-mean_r) for k in range(n))
    den = (sum((rs[k]-mean_s)**2 for k in range(n)) * sum((rr[k]-mean_r)**2 for k in range(n))) ** 0.5
    ic = num/den if den > 0 else 0

    results.setdefault(yr, []).append(ic)
    if (i+1) % 100 == 0:
        print(f"  處理 {i+1}/{len(days)}", flush=True)

db.close()

print("\n" + "="*60)
print("IC 分年統計(分數 vs 未來20日報酬,Spearman 等級相關)")
print("="*60)
print(f"{'年份':<8}{'樣本天數':>10}{'平均IC':>10}{'IC標準差':>10}{'ICIR':>10}{'正IC比例':>10}")
print("-"*60)
for yr in sorted(results.keys()):
    ics = results[yr]
    mu = st.mean(ics)
    sd = st.pstdev(ics) if len(ics) > 1 else 0
    icir = mu/sd if sd > 1e-9 else 0
    pos = sum(1 for x in ics if x > 0) / len(ics) * 100
    print(f"{yr:<8}{len(ics):>10}{mu:>10.4f}{sd:>10.4f}{icir:>10.2f}{pos:>9.1f}%")
print("="*60)
print("\n判讀:")
print("  IC > 0.03  = 有預測力(量化業界常見門檻)")
print("  IC > 0.05  = 相當不錯")
print("  ICIR > 0.5 = 訊號穩定")
print("  正IC比例 > 55% = 方向性一致")
