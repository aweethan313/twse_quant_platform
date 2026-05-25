"""scripts/v5_migrate.py - technical_daily_features 建表"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS technical_daily_features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
        rsi14 REAL,
        macd REAL, macd_signal REAL, macd_hist REAL,
        volume_ma5 REAL, volume_ma20 REAL,
        return_1d REAL, return_5d REAL, return_20d REAL,
        atr14 REAL,
        distance_ma20 REAL,
        high_20d REAL, low_20d REAL,
        volatility_20d REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(code, trade_date)
    )""")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_tdf_date ON technical_daily_features(trade_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tdf_code ON technical_daily_features(code)")

    # strategy_decision_logs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strategy_decision_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER,
        strategy_name TEXT,
        mode TEXT DEFAULT 'forward_paper',
        signal_date TEXT,
        data_cutoff_time TEXT,
        execution_date TEXT,
        execution_time_model TEXT DEFAULT 'next_day_0910_odd_lot',
        code TEXT,
        action TEXT,
        candidate_score REAL,
        technical_score REAL,
        chip_score REAL,
        fundamental_score REAL,
        risk_score REAL,
        final_score REAL,
        suggested_shares INTEGER,
        reference_price REAL,
        expected_fill_price REAL,
        stop_loss REAL,
        target_price REAL,
        is_blocked INTEGER DEFAULT 0,
        blocked_reason TEXT,
        reason_summary TEXT,
        no_lookahead_pass INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_sdl_date ON strategy_decision_logs(signal_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sdl_account ON strategy_decision_logs(account_id)")

    conn.commit()
    conn.close()
    print("✓ technical_daily_features 建立")
    print("✓ strategy_decision_logs 建立")

if __name__ == "__main__":
    print(f"DB: {DB}")
    migrate()
