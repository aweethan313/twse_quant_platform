path = 'scripts/daily_pipeline.py'
with open(path) as f:
    c = f.read()
if 'credit_dividends' in c:
    print("✓ pipeline 已修,跳過"); raise SystemExit
old = '''        r = generate_strategy_decisions(target_date)
        simulate_paper_fills(target_date)
        update_v5_equity(target_date)'''
new = '''        from backend.v5.dividends import refresh_corporate_actions, credit_dividends
        refresh_corporate_actions(target_date)
        r = generate_strategy_decisions(target_date)
        simulate_paper_fills(target_date)
        credit_dividends(target_date)
        update_v5_equity(target_date)'''
if old in c:
    with open(path, 'w') as f:
        f.write(c.replace(old, new, 1))
    print("✓ daily_pipeline 已掛入除息入帳(fills後、equity前)")
else:
    print("❌ 錨點失敗")

path2 = 'scripts/process_pending_backfill.py'
with open(path2) as f:
    c2 = f.read()
if 'credit_dividends' not in c2:
    old2 = '''    simulate_paper_fills(d)
    update_v5_equity(d)'''
    new2 = '''    simulate_paper_fills(d)
    from backend.v5.dividends import credit_dividends
    credit_dividends(d)
    update_v5_equity(d)'''
    if old2 in c2:
        with open(path2, 'w') as f:
            f.write(c2.replace(old2, new2, 1))
        print("✓ process_pending_backfill 也已掛入")
    else:
        print("❌ backfill 錨點失敗")
else:
    print("✓ backfill 已修,跳過")
