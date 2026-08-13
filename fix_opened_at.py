"""
修正兩個影響「20日到期換股」的 bug:

Bug A: positions.opened_at 寫入 datetime('now') = 執行當下的系統時間
       → 回測時所有持倉都記成回測執行日,held_days 永遠算 0,到期換股從不觸發
       → 改為寫入 execution_date(該筆買進的實際成交日)

Bug B: held_days 用 code='2330' 當交易日曆
       → 若 2330 停牌或資料缺失就算錯
       → 改用 trading_calendar(is_open=1)

用法:python3 fix_opened_at.py
"""

# ── Bug A: paper_engine ──
path = 'backend/v5/paper_engine.py'
with open(path) as f:
    c = f.read()

if 'OPENED_AT_FIX' in c:
    print("✓ paper_engine 已修")
else:
    old = '''                        INSERT INTO positions (account_id, code, lots, avg_cost, opened_at)
                        VALUES (:id, :c, :lots, :cost, datetime('now','localtime'))
                    """), {"id": aid, "c": code, "lots": shares_int,
                           "cost": fill_price})'''
    new = '''                        -- OPENED_AT_FIX:用成交日,不可用 datetime('now')
                        -- (原本寫執行當下時間,回測時 held_days 永遠算 0,到期換股從不觸發)
                        INSERT INTO positions (account_id, code, lots, avg_cost, opened_at)
                        VALUES (:id, :c, :lots, :cost, :oa)
                    """), {"id": aid, "c": code, "lots": shares_int,
                           "cost": fill_price, "oa": str(execution_date)})'''
    if old in c:
        with open(path, 'w') as f:
            f.write(c.replace(old, new, 1))
        print("✓ paper_engine: opened_at 改用 execution_date")
    else:
        print("❌ paper_engine 錨點失敗")

# ── Bug B: decision_engine 的 held_days ──
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    c = f.read()

if 'HELD_DAYS_CALENDAR' in c:
    print("✓ decision_engine 已修")
else:
    old = '''                    held_days = db.execute(text("""
                        SELECT COUNT(DISTINCT trade_date) FROM ohlcv_daily
                        WHERE code='2330' AND trade_date > :o AND trade_date <= :d
                    """), {"o": str(opened_at)[:10], "d": str(signal_date)}).scalar() or 0'''
    new = '''                    # HELD_DAYS_CALENDAR:用交易日曆計算,不可寫死單一股票
                    held_days = db.execute(text("""
                        SELECT COUNT(*) FROM trading_calendar
                        WHERE is_open=1 AND trade_date > :o AND trade_date <= :d
                    """), {"o": str(opened_at)[:10], "d": str(signal_date)}).scalar() or 0'''
    if old in c:
        with open(path, 'w') as f:
            f.write(c.replace(old, new, 1))
        print("✓ decision_engine: held_days 改用 trading_calendar")
    else:
        print("❌ decision_engine 錨點失敗")

print("\n請執行:python3 -m py_compile backend/v5/paper_engine.py backend/v5/decision_engine.py")
