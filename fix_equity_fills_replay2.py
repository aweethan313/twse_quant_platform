"""
update_v5_equity 改用 fills 重播 v2(按行定位,避開註解順序差異)
持倉與現金都從 paper_fills 重播出「該日收盤狀態」,不再讀 positions / accounts.cash
用法:python3 fix_equity_fills_replay2.py
"""
path = 'backend/v5/paper_engine.py'
with open(path) as f:
    lines = f.readlines()

if any('FILLS_REPLAY_EQUITY' in l for l in lines):
    print("✓ 已修,跳過")
    raise SystemExit

# 定位:找 "for aid, cash, init_cash in accounts:" 那行
start = None
for i, l in enumerate(lines):
    if 'for aid, cash, init_cash in accounts:' in l:
        start = i
        break
if start is None:
    print("❌ 找不到 for 迴圈起點")
    raise SystemExit

# 找結尾:該迴圈內第一個 "total = cash_f + float(mkt)"
end = None
for j in range(start + 1, min(start + 60, len(lines))):
    if 'total = cash_f + float(mkt)' in lines[j]:
        end = j
        break
if end is None:
    print("❌ 找不到 total = cash_f + float(mkt)")
    raise SystemExit

print(f"將取代第 {start+2} ~ {end} 行(迴圈內、total 之前的計算段)")

new_block = '''            init_f = float(init_cash or 200000)
            # FILLS_REPLAY_EQUITY:持倉與現金都由 paper_fills 重播出「該日收盤狀態」
            # (不可用 positions / accounts.cash —— 那是當前快照,歷史重算會用到未來狀態)
            fills = db.execute(text("""
                SELECT code, action, shares, fill_price, fee, tax, gross_amount, net_amount
                FROM paper_fills
                WHERE account_id=:id AND execution_date <= :d
                  AND COALESCE(is_blocked,0)=0
                ORDER BY execution_date, id
            """), {"id": aid, "d": str(snap_date)}).fetchall()

            cash_f = init_f
            holdings = {}
            for f_code, f_act, f_sh, f_px, f_fee, f_tax, f_gross, f_net in fills:
                f_sh = float(f_sh or 0); f_px = float(f_px or 0)
                if f_sh <= 0:
                    continue
                if f_act == 'BUY':
                    cost = float(f_gross) if f_gross else f_px * f_sh
                    cash_f -= (cost + float(f_fee or 0))
                    holdings[f_code] = holdings.get(f_code, 0.0) + f_sh
                elif f_act == 'SELL':
                    held = holdings.get(f_code, 0.0)
                    if held <= 0:
                        continue
                    sell_sh = min(f_sh, held)
                    proceeds = float(f_net) if f_net else f_px * sell_sh - float(f_fee or 0) - float(f_tax or 0)
                    cash_f += proceeds
                    holdings[f_code] = held - sell_sh

            div = db.execute(text("""
                SELECT COALESCE(SUM(amount),0) FROM dividend_income
                WHERE account_id=:id AND ex_date <= :d
            """), {"id": aid, "d": str(snap_date)}).scalar() or 0
            cash_f += float(div)

            mkt = 0.0
            for h_code, h_sh in holdings.items():
                if h_sh <= 0.5:
                    continue
                px = db.execute(text("""
                    SELECT COALESCE(
                        (SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d),
                        (SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date<=:d
                         ORDER BY trade_date DESC LIMIT 1)
                    )
                """), {"c": h_code, "d": str(snap_date)}).scalar()
                if px:
                    mkt += h_sh * float(px)

'''

new_lines = lines[:start+1] + [new_block] + lines[end:]
with open(path, 'w') as f:
    f.writelines(new_lines)

print("✓ update_v5_equity 已改用 fills 重播(持倉+現金+股息)")
print("  請執行:python3 -m py_compile backend/v5/paper_engine.py")
