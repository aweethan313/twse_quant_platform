"""
update_v5_equity 改用 fills 重播(選項A:回測與實測共用同一套邏輯)

原問題:
- 持倉讀 positions 表(當前快照)→ 算歷史淨值時用的是「未來的持倉」
- 現金讀 strategy_accounts.cash(當前現金)→ 同樣是未來狀態
  在每日運作時正確(當前=今天),但任何歷史重算都失真,回測完全無效。

改為:
- 持倉 = 重播 execution_date <= snap_date 的 fills(BUY - SELL)
- 現金 = initial_cash - 買入支出(含費) + 賣出收入(扣費稅) + ex_date <= snap_date 的股息
- 價格仍用 snap_date 當天(無報價 fallback 該股最近一日)

用法:python3 fix_equity_fills_replay.py
"""
path = 'backend/v5/paper_engine.py'
with open(path) as f:
    c = f.read()

if 'FILLS_REPLAY_EQUITY' in c:
    print("✓ 已修,跳過")
    raise SystemExit

old = '''        for aid, cash, init_cash in accounts:
            cash_f = float(cash or init_cash or 200000)
            init_f = float(init_cash or 200000)
            # 計算持倉市值
            # SNAPDATE_PRICE:用 snap_date 當天價格(無報價則取該股 <= snap_date 最近一日)
            mkt = db.execute(text("""
                SELECT SUM(p.lots * COALESCE(o.close, ofb.close))
                FROM positions p
                LEFT JOIN ohlcv_daily o
                       ON o.code=p.code AND o.trade_date=:d
                LEFT JOIN ohlcv_daily ofb
                       ON ofb.code=p.code
                      AND ofb.trade_date=(
                            SELECT MAX(trade_date) FROM ohlcv_daily
                            WHERE code=p.code AND trade_date <= :d)
                WHERE p.account_id=:id
            """), {"id": aid, "d": str(snap_date)}).scalar() or 0
            total = cash_f + float(mkt)'''

new = '''        for aid, cash, init_cash in accounts:
            init_f = float(init_cash or 200000)
            # FILLS_REPLAY_EQUITY:持倉與現金都由 paper_fills 重播出「該日收盤狀態」
            # (不可用 positions/accounts.cash —— 那是當前快照,歷史重算會用到未來狀態)
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

            # 該日之前(含當日)已除息的現金股利
            div = db.execute(text("""
                SELECT COALESCE(SUM(amount),0) FROM dividend_income
                WHERE account_id=:id AND ex_date <= :d
            """), {"id": aid, "d": str(snap_date)}).scalar() or 0
            cash_f += float(div)

            # 用 snap_date 當天價格計算市值(無報價則取該股 <= snap_date 最近一日)
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

            total = cash_f + float(mkt)'''

if old in c:
    with open(path, 'w') as f:
        f.write(c.replace(old, new, 1))
    print("✓ update_v5_equity 已改用 fills 重播(持倉+現金+股息)")
    print("  請執行:python3 -m py_compile backend/v5/paper_engine.py")
else:
    print("❌ 錨點失敗,請把 paper_engine.py 246-300 行貼給 Claude")
