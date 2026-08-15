"""驗證停損後的股價走勢:是被洗出場,還是成功避險?"""
from sqlalchemy import text
from backend.models.database import SessionLocal
db = SessionLocal()

# 重播 104 的 fills,找出所有賣出點與虧損幅度
fills = db.execute(text("""
    SELECT code, action, shares, fill_price, fee, tax, gross_amount, net_amount, execution_date
    FROM paper_fills WHERE account_id=104 AND COALESCE(is_blocked,0)=0
    ORDER BY execution_date, id""")).fetchall()

pos, sells = {}, []
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
        pnl_pct = (proceeds - avg*sell_sh) / (avg*sell_sh) * 100 if avg*sell_sh > 0 else 0
        sells.append((code, str(ed), fp, pnl_pct))
        pos[code] = (s-sell_sh, c-avg*sell_sh)

# 對每筆賣出,查賣出後 20/60 日的股價變化
def price_after(code, d, ndays):
    r = db.execute(text("""
        SELECT close FROM ohlcv_daily
        WHERE code=:c AND trade_date > :d ORDER BY trade_date LIMIT 1 OFFSET :n
    """), {"c": code, "d": d, "n": ndays-1}).scalar()
    return float(r) if r else None

groups = {"停損類(虧>5%)": [], "小賺小賠": [], "獲利了結(賺>10%)": []}
for code, ed, sell_px, pnl_pct in sells:
    p20 = price_after(code, ed, 20)
    p60 = price_after(code, ed, 60)
    if p20 is None: continue
    chg20 = (p20/sell_px - 1) * 100
    chg60 = (p60/sell_px - 1) * 100 if p60 else None
    key = "停損類(虧>5%)" if pnl_pct < -5 else ("獲利了結(賺>10%)" if pnl_pct > 10 else "小賺小賠")
    groups[key].append((code, ed, pnl_pct, chg20, chg60))

print("="*72)
print("賣出後的股價走勢(賣出價 → 之後 N 日收盤)")
print("="*72)
for k, items in groups.items():
    if not items: continue
    n = len(items)
    avg20 = sum(x[3] for x in items) / n
    v60 = [x[4] for x in items if x[4] is not None]
    avg60 = sum(v60)/len(v60) if v60 else 0
    up20 = sum(1 for x in items if x[3] > 0) / n * 100
    print(f"\n【{k}】{n} 筆")
    print(f"  賣出後20日平均漲跌: {avg20:+.1f}%  (上漲比例 {up20:.0f}%)")
    print(f"  賣出後60日平均漲跌: {avg60:+.1f}%  ({len(v60)} 筆有資料)")

print()
print("="*72)
print("停損類明細(最多顯示15筆)")
print("="*72)
print(f"{'代號':<8}{'賣出日':<13}{'損益%':>9}{'後20日':>10}{'後60日':>10}")
print("-"*72)
for code, ed, pnl, c20, c60 in groups["停損類(虧>5%)"][:15]:
    s60 = f"{c60:+9.1f}%" if c60 is not None else "       —"
    print(f"{code:<8}{ed:<13}{pnl:>8.1f}%{c20:>9.1f}%{s60}")
db.close()
