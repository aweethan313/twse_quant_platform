"""
把 A7 (MLTop5) 的 max_hold_days 改成 5（對應 ML 的 5 日預測）。
其他帳戶維持原設定。idempotent。
用法：python3 set_a7_hold_5days.py
"""
import sqlite3
con = sqlite3.connect('data/db/quant.db')
cur = con.execute("SELECT max_hold_days FROM strategy_account_configs WHERE account_id=17").fetchone()
print(f"A7 目前 max_hold_days = {cur[0]}")
con.execute("UPDATE strategy_account_configs SET max_hold_days=5 WHERE account_id=17")
con.commit()
new = con.execute("SELECT max_hold_days FROM strategy_account_configs WHERE account_id=17").fetchone()
print(f"A7 已改為 max_hold_days = {new[0]}")
con.close()
