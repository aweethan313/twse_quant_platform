"""scripts/v6_migrate_c.py - V6C 資料庫 migration"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 1. trading_calendar（如果還沒建）
    cur.execute("""CREATE TABLE IF NOT EXISTS trading_calendar (
        trade_date TEXT PRIMARY KEY,
        is_open INTEGER DEFAULT 1,
        weekday INTEGER,
        source TEXT DEFAULT 'ohlcv_daily',
        note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ trading_calendar")

    # 2. benchmark_daily_equity 加欄位
    bc = [r[1] for r in cur.execute("PRAGMA table_info(benchmark_daily_equity)").fetchall()]
    for col, defn in [("is_valid","INTEGER DEFAULT 1"),
                      ("anomaly_reason","TEXT"),
                      ("equity","REAL"),
                      ("cumulative_return","REAL")]:
        if col not in bc:
            cur.execute(f"ALTER TABLE benchmark_daily_equity ADD COLUMN {col} {defn}")
            print(f"  ✓ benchmark_daily_equity.{col}")

    # rename snap_date → trade_date if needed
    # (keep both for compatibility)

    # 3. paper_fills 加欄位
    pf = [r[1] for r in cur.execute("PRAGMA table_info(paper_fills)").fetchall()]
    for col, defn in [("fill_source","TEXT DEFAULT 'daily_simulated'"),
                      ("is_estimated","INTEGER DEFAULT 1"),
                      ("price_mode","TEXT DEFAULT 'next_open'"),
                      ("fallback_reason","TEXT"),
                      ("is_blocked","INTEGER DEFAULT 0"),
                      ("blocked_reason","TEXT")]:
        if col not in pf:
            cur.execute(f"ALTER TABLE paper_fills ADD COLUMN {col} {defn}")
            print(f"  ✓ paper_fills.{col}")

    # 4. tomorrow_trade_plans 確保有 status PENDING_MANUAL_FILL
    ttp = [r[1] for r in cur.execute("PRAGMA table_info(tomorrow_trade_plans)").fetchall()
           if r[1] == 'status']
    # 欄位已存在，只需確保 status 有 PENDING_MANUAL_FILL 預設
    # (不改 schema，只在 code 層面處理)

    conn.commit()
    conn.close()
    print("✓ V6C migration 完成")

if __name__ == "__main__":
    migrate()
