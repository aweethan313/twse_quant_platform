path = 'backend/v5/benchmark.py'
with open(path) as f:
    c = f.read()
if 'div_rows' in c:
    print("✓ benchmark 已修,跳過"); raise SystemExit
old = '''        prev_equity = initial_cash
        updated = 0
        for trade_date, open_p, close_p, volume in rows:
            d = str(trade_date)
            price = _adjusted_price(d, close_p)
            if not price:
                continue
            equity = shares * price'''
new = '''        # 含息:累加除息日起的每股現金股利(金額待公告者暫為0,公布後每日重建自動補上)
        div_rows = db.execute(text("""
            SELECT ex_date, COALESCE(cash_dividend, 0) FROM corporate_actions
            WHERE code=:c AND ex_date >= :sd ORDER BY ex_date
        """), {"c": benchmark_code, "sd": start_date}).fetchall()

        prev_equity = initial_cash
        updated = 0
        for trade_date, open_p, close_p, volume in rows:
            d = str(trade_date)
            price = _adjusted_price(d, close_p)
            if not price:
                continue
            div_cum = sum(float(v) for ex, v in div_rows if str(ex) <= d)
            equity = shares * (price + div_cum)'''
if old in c:
    with open(path, 'w') as f:
        f.write(c.replace(old, new, 1))
    print("✓ benchmark 改為含息報酬(total return)")
else:
    print("❌ 錨點失敗")
