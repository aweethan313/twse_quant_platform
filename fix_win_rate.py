path = 'main.py'
with open(path) as f:
    c = f.read()
if '重播 fills 歷史' in c:
    print("✓ 已修，跳過"); raise SystemExit

old = '''        # 成交統計
        fills = db.execute(_t("""
            SELECT action, fill_price, shares,
                   (SELECT AVG(avg_cost) FROM positions WHERE account_id=:id AND code=pf.code) as avg_cost
            FROM paper_fills pf WHERE account_id=:id AND action='SELL'
        """), {"id": account_id}).fetchall()

        trade_count = len(fills)
        wins = losses = 0
        win_pnl = loss_pnl = 0
        for _, fp, shares, avg_cost in fills:
            if avg_cost and avg_cost > 0:
                pnl = (float(fp) - float(avg_cost)) * float(shares)
                if pnl > 0: wins += 1; win_pnl += pnl
                else: losses += 1; loss_pnl += abs(pnl)

        win_rate = wins / trade_count * 100 if trade_count > 0 else 0
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else (99.0 if win_pnl > 0 else 0)'''

new = '''        # 成交統計：重播 fills 歷史算已實現損益（不可查 positions——賣出後已刪）
        fills = db.execute(_t("""
            SELECT code, action, fill_price, shares, fee, tax, gross_amount, net_amount
            FROM paper_fills WHERE account_id=:id AND COALESCE(is_blocked,0)=0
            ORDER BY execution_date, id
        """), {"id": account_id}).fetchall()

        _pos = {}
        trade_count = 0
        wins = losses = 0
        win_pnl = loss_pnl = 0.0
        for code, action, fp, sh, fee, tax, gross, net in fills:
            fp = float(fp or 0); sh = float(sh or 0)
            if sh <= 0:
                continue
            if action == 'BUY':
                s, cst = _pos.get(code, (0.0, 0.0))
                cost = float(gross) if gross else fp * sh
                _pos[code] = (s + sh, cst + cost + float(fee or 0))
            elif action == 'SELL':
                s, cst = _pos.get(code, (0.0, 0.0))
                if s <= 0:
                    continue
                avg = cst / s
                sell_sh = min(sh, s)
                proceeds = float(net) if net else fp * sell_sh - float(fee or 0) - float(tax or 0)
                pnl = proceeds - avg * sell_sh
                trade_count += 1
                if pnl > 0:
                    wins += 1; win_pnl += pnl
                else:
                    losses += 1; loss_pnl += abs(pnl)
                _pos[code] = (s - sell_sh, cst - avg * sell_sh)

        win_rate = wins / trade_count * 100 if trade_count > 0 else 0
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else (99.0 if win_pnl > 0 else 0)'''

if old in c:
    c = c.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ 勝率計算已修正（重播 fills 歷史）")
else:
    print("❌ 找不到錨點")
