"""診斷 2026:IC 最好但績效落後的原因"""
from sqlalchemy import text
from backend.models.database import SessionLocal
db = SessionLocal()

print("="*60)
print("A. 104 在 2026 的交易行為")
print("="*60)
rows = db.execute(text("""
    SELECT action, COUNT(*) n, ROUND(SUM(fee+tax)) cost
    FROM paper_fills WHERE account_id=104 AND execution_date >= '2026-01-01'
    GROUP BY action""")).fetchall()
for a, n, c in rows:
    print(f"  {a}: {n} 筆, 費用 {c}")

print()
print("="*60)
print("B. 已實現損益分布(2026)")
print("="*60)
fills = db.execute(text("""
    SELECT code, action, shares, fill_price, fee, tax, gross_amount, net_amount, execution_date
    FROM paper_fills WHERE account_id=104 AND COALESCE(is_blocked,0)=0
    ORDER BY execution_date, id""")).fetchall()
pos, trades = {}, []
for code, act, sh, fp, fee, tax, gross, net, ed in fills:
    sh = float(sh or 0); fp = float(fp or 0)
    if sh <= 0: continue
    if act == 'BUY':
        s, c = pos.get(code, (0.0, 0.0))
        cost = float(gross) if gross else fp*sh
        pos[code] = (s+sh, c+cost+float(fee or 0))
    else:
        s, c = pos.get(code, (0.0, 0.0))
        if s <= 0: continue
        sell_sh = min(sh, s); avg = c/s
        proceeds = float(net) if net else fp*sell_sh-float(fee or 0)-float(tax or 0)
        pnl = proceeds - avg*sell_sh
        pnl_pct = pnl / (avg*sell_sh) * 100 if avg*sell_sh > 0 else 0
        if str(ed) >= '2026-01-01':
            trades.append((code, str(ed), pnl, pnl_pct))
        pos[code] = (s-sell_sh, c-avg*sell_sh)

wins = [t for t in trades if t[2] > 0]
losses = [t for t in trades if t[2] <= 0]
print(f"  平倉 {len(trades)} 筆 | 賺 {len(wins)} 賠 {len(losses)}")
if trades:
    print(f"  勝率: {len(wins)/len(trades)*100:.1f}%")
    if wins: print(f"  平均獲利: {sum(t[2] for t in wins)/len(wins):,.0f} ({sum(t[3] for t in wins)/len(wins):+.1f}%)")
    if losses: print(f"  平均虧損: {sum(t[2] for t in losses)/len(losses):,.0f} ({sum(t[3] for t in losses)/len(losses):+.1f}%)")
    tw = sum(t[2] for t in wins); tl = abs(sum(t[2] for t in losses))
    print(f"  獲利因子 PF: {tw/tl:.2f}" if tl > 0 else "  PF: inf")
    print(f"  已實現總損益: {sum(t[2] for t in trades):,.0f}")

print()
print("="*60)
print("C. 2026 選到的股票 vs 全市場(平均漲幅比較)")
print("="*60)
picked = db.execute(text("""
    SELECT DISTINCT code FROM paper_fills
    WHERE account_id=104 AND action='BUY' AND execution_date >= '2026-01-01'""")).fetchall()
codes = [r[0] for r in picked]
if codes:
    ph = ",".join(f"'{c}'" for c in codes)
    r1 = db.execute(text(f"""
        SELECT ROUND(AVG(e.close/s.close-1)*100,1) FROM ohlcv_daily e
        JOIN ohlcv_daily s ON s.code=e.code AND s.trade_date='2026-01-02'
        WHERE e.trade_date='2026-05-22' AND e.code IN ({ph}) AND s.close>0""")).scalar()
    r2 = db.execute(text("""
        SELECT ROUND(AVG(e.close/s.close-1)*100,1) FROM ohlcv_daily e
        JOIN ohlcv_daily s ON s.code=e.code AND s.trade_date='2026-01-02'
        WHERE e.trade_date='2026-05-22' AND e.code GLOB '[0-9][0-9][0-9][0-9]' AND s.close>0""")).scalar()
    print(f"  策略買過的 {len(codes)} 檔,平均漲幅: {r1}%")
    print(f"  全市場平均漲幅: {r2}%")
    print(f"  → 選股本身{'優於' if (r1 or 0) > (r2 or 0) else '落後'}市場 {abs((r1 or 0)-(r2 or 0)):.1f}pt")
db.close()
