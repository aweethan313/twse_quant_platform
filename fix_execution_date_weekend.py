"""
修正 execution_date 排到週末的 bug(每個週五的決策都因此從未執行)

原問題:
  next_day = SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > signal_date
  週五晚上跑時,資料庫最新就是當天,查不到 → fallback signal_date + 1天 = 週六
  → simulate_paper_fills 只在交易日跑 → 該批決策永遠不執行

實測影響:2026-05-25 起 6 個週五、266 筆決策全部作廢

修法:
  1. 優先用 trading_calendar 找下一個 is_open=1 的日子(可涵蓋未來)
  2. 再用 ohlcv_daily(原邏輯)
  3. fallback 改為「往後找第一個非週末日」,不再無腦 +1

用法:python3 fix_execution_date_weekend.py
"""
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    c = f.read()

if 'EXEC_DATE_WEEKEND_FIX' in c:
    print("✓ 已修,跳過")
    raise SystemExit

old = '''        # 找下一個交易日
        next_day = db.execute(text("""
            SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > :d
        """), {"d": str(signal_date)}).scalar()'''

new = '''        # EXEC_DATE_WEEKEND_FIX:找下一個交易日
        # (原本查不到時 fallback 為 +1 天,週五會排到週六 → 該批決策永遠不執行)
        next_day = db.execute(text("""
            SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > :d
        """), {"d": str(signal_date)}).scalar()
        if not next_day:
            # 交易日曆可能已排到未來,優先採用
            next_day = db.execute(text("""
                SELECT MIN(trade_date) FROM trading_calendar
                WHERE trade_date > :d AND is_open=1
            """), {"d": str(signal_date)}).scalar()
        if not next_day:
            # 最後保險:往後找第一個非週末日(週五 → 下週一)
            _probe = signal_date + timedelta(days=1)
            while _probe.weekday() >= 5:
                _probe = _probe + timedelta(days=1)
            next_day = str(_probe)'''

if old in c:
    with open(path, 'w') as f:
        f.write(c.replace(old, new, 1))
    print("✓ execution_date 週末 bug 已修")
    print("  請執行:python3 -m py_compile backend/v5/decision_engine.py")
else:
    print("❌ 錨點失敗,請把 decision_engine.py 27-32 行貼給 Claude")
