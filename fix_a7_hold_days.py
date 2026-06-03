"""
設計A：讓 max_hold_days 生效——持有滿 N 個交易日就到期換股。
適用所有有設 max_hold_days 的策略帳戶（含 A7 MLTop5）。
  1. pos_map 查詢加入 opened_at
  2. 賣出邏輯加入「持有交易日數 >= max_hold_days → 賣出」（順序在停損停利之前）
持有天數用「交易日」計算（查 ohlcv_daily 的 distinct trade_date），不含假日。
idempotent。用法：python3 fix_a7_hold_days.py
"""
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    c = f.read()

if 'max_hold_days 到期' in c:
    print("✓ 已套用設計A，跳過")
    raise SystemExit

# 1. pos_map 查詢加 opened_at
old_q = '''                SELECT code, lots, avg_cost FROM positions WHERE account_id=:id'''
new_q = '''                SELECT code, lots, avg_cost, opened_at FROM positions WHERE account_id=:id'''
old_map = '''            pos_map = {r[0]: {"lots": r[1], "avg_cost": float(r[2] or 0)} for r in positions}'''
new_map = '''            pos_map = {r[0]: {"lots": r[1], "avg_cost": float(r[2] or 0), "opened_at": r[3]} for r in positions}'''

if old_q in c and old_map in c:
    c = c.replace(old_q, new_q, 1)
    c = c.replace(old_map, new_map, 1)
    print("✓ pos_map 已加入 opened_at")
else:
    print("❌ 找不到 pos_map 查詢，請貼 70 行附近給 Claude")
    raise SystemExit

# 2. 賣出邏輯加入持有天數判斷（在停損判斷之前）
old_sell = '''                pnl_pct = (sell_price / avg_cost - 1) * 100
                sell_action = None
                sell_reason = None

                if pnl_pct <= -cfg["stop_loss_pct"] * 100:'''

new_sell = '''                pnl_pct = (sell_price / avg_cost - 1) * 100
                sell_action = None
                sell_reason = None

                # 設計A：持有天數判斷（用交易日計算，不含假日）
                max_hold = cfg.get("max_hold_days")
                opened_at = pos.get("opened_at")
                if max_hold and opened_at:
                    held_days = db.execute(text("""
                        SELECT COUNT(DISTINCT trade_date) FROM ohlcv_daily
                        WHERE code='2330' AND trade_date > :o AND trade_date <= :d
                    """), {"o": str(opened_at)[:10], "d": str(signal_date)}).scalar() or 0
                    if held_days >= max_hold:
                        sell_action = "SELL"
                        sell_reason = f"max_hold_days 到期（持有{held_days}交易日 >= {max_hold}），換股"

                if sell_action is None and pnl_pct <= -cfg["stop_loss_pct"] * 100:'''

if old_sell in c:
    c = c.replace(old_sell, new_sell, 1)
    print("✓ 賣出邏輯已加入 max_hold_days 到期換股（順序：天數→停損→停利）")
else:
    print("❌ 找不到賣出邏輯，請貼 200 行附近給 Claude")
    raise SystemExit

with open(path, 'w') as f:
    f.write(c)
print("\n完成。max_hold_days 現在會生效：持有滿 N 交易日就賣出換股。")
print("A7 設定 max_hold_days=7，但你想要 5 天的話，跑下面的 SQL 改設定：")
