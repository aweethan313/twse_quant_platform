"""scripts/v5_migrate.py
V5 資料庫 migration：
1. strategy_account_configs
2. benchmark_daily_equity
3. paper_fills
4. trade_logs 加 mode / signal_date 欄位
5. strategy_accounts 加 mode / realized_pnl / unrealized_pnl 欄位
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    print(f"DB: {DB}")

    # ── 1. strategy_account_configs ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strategy_account_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER UNIQUE NOT NULL,
        strategy_name TEXT NOT NULL,
        mode TEXT DEFAULT 'forward_paper',
        candidate_rank_limit INTEGER DEFAULT 5,
        min_score REAL DEFAULT 75.0,
        max_positions INTEGER DEFAULT 5,
        max_position_pct REAL DEFAULT 0.20,
        stop_loss_pct REAL DEFAULT 0.08,
        take_profit_pct REAL DEFAULT 0.15,
        min_hold_days INTEGER DEFAULT 1,
        max_hold_days INTEGER DEFAULT 20,
        allow_core_stocks INTEGER DEFAULT 1,
        allow_small_caps INTEGER DEFAULT 0,
        theme_filter TEXT DEFAULT NULL,
        large_cap_only INTEGER DEFAULT 0,
        no_chase_enabled INTEGER DEFAULT 0,
        max_rsi14 REAL DEFAULT 80.0,
        min_rsi14 REAL DEFAULT 30.0,
        max_distance_ma20_pct REAL DEFAULT 12.0,
        target_0050_pct REAL DEFAULT 0.0,
        target_satellite_pct REAL DEFAULT 1.0,
        market_risk_position_multiplier REAL DEFAULT 1.0,
        description TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ strategy_account_configs")

    # ── 2. benchmark_daily_equity ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS benchmark_daily_equity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_code TEXT NOT NULL DEFAULT '0050',
        snap_date TEXT NOT NULL,
        price REAL,
        shares REAL,
        equity REAL,
        daily_return REAL,
        cumulative_return REAL,
        initial_equity REAL DEFAULT 200000,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(benchmark_code, snap_date)
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bde_date ON benchmark_daily_equity(snap_date)")
    print("✓ benchmark_daily_equity")

    # ── 3. paper_fills ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        plan_id INTEGER,
        strategy_name TEXT,
        signal_date TEXT,
        execution_date TEXT,
        code TEXT NOT NULL,
        stock_name TEXT,
        action TEXT NOT NULL,
        shares INTEGER NOT NULL,
        fill_price REAL NOT NULL,
        fill_time TEXT,
        fill_source TEXT DEFAULT 'simulated',
        execution_time_model TEXT DEFAULT 'next_day_open_slippage',
        fee REAL DEFAULT 0,
        tax REAL DEFAULT 0,
        slippage REAL DEFAULT 0,
        gross_amount REAL,
        net_amount REAL,
        note TEXT,
        no_lookahead_pass INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pf_account ON paper_fills(account_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pf_date ON paper_fills(execution_date)")
    print("✓ paper_fills")

    # ── 4. trade_logs 加欄位 ──
    existing_cols = [r[1] for r in cur.execute("PRAGMA table_info(trade_logs)").fetchall()]
    new_cols = [
        ("mode", "TEXT DEFAULT 'forward_paper'"),
        ("signal_date", "TEXT"),
        ("execution_date", "TEXT"),
        ("fill_source", "TEXT DEFAULT 'simulated'"),
        ("no_lookahead_pass", "INTEGER DEFAULT 1"),
    ]
    for col, coldef in new_cols:
        if col not in existing_cols:
            cur.execute(f"ALTER TABLE trade_logs ADD COLUMN {col} {coldef}")
            print(f"✓ trade_logs.{col}")

    # ── 5. strategy_accounts 加欄位 ──
    existing_cols2 = [r[1] for r in cur.execute("PRAGMA table_info(strategy_accounts)").fetchall()]
    new_cols2 = [
        ("mode", "TEXT DEFAULT 'forward_paper'"),
        ("realized_pnl", "REAL DEFAULT 0"),
        ("unrealized_pnl", "REAL DEFAULT 0"),
        ("benchmark", "TEXT DEFAULT '0050'"),
        ("start_date", "TEXT"),
        ("latest_signal_date", "TEXT"),
        ("latest_execution_date", "TEXT"),
    ]
    for col, coldef in new_cols2:
        if col not in existing_cols2:
            cur.execute(f"ALTER TABLE strategy_accounts ADD COLUMN {col} {coldef}")
            print(f"✓ strategy_accounts.{col}")

    conn.commit()
    conn.close()
    print("\n✅ V5 migration 完成")

if __name__ == "__main__":
    migrate()
