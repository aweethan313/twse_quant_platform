"""scripts/v8_migrate.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings
DB = str(settings.DB_PATH)

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 1. ML 評分結果
    cur.execute("""CREATE TABLE IF NOT EXISTS ml_score_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        score_date TEXT NOT NULL,
        code TEXT NOT NULL,
        stock_name TEXT,
        ml_score REAL,
        ml_rank INTEGER,
        feature_importance TEXT,
        model_version TEXT,
        predicted_return_5d REAL,
        confidence REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(score_date, code)
    )""")
    print("✓ ml_score_results")

    # 2. 週績效快照
    cur.execute("""CREATE TABLE IF NOT EXISTS weekly_performance_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT NOT NULL,
        week_end TEXT NOT NULL,
        account_id INTEGER,
        strategy_name TEXT,
        equity_start REAL,
        equity_end REAL,
        weekly_return REAL,
        benchmark_return REAL,
        alpha REAL,
        max_drawdown REAL,
        trade_count INTEGER,
        win_rate REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(week_start, account_id)
    )""")
    print("✓ weekly_performance_snapshots")

    # 3. 選股集中度
    cur.execute("""CREATE TABLE IF NOT EXISTS selection_concentration (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_date TEXT NOT NULL,
        code TEXT NOT NULL,
        stock_name TEXT,
        selected_by_count INTEGER,
        selected_by_accounts TEXT,
        concentration_risk TEXT,
        correlation_score REAL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ selection_concentration")

    # 4. 月營收
    cur.execute("""CREATE TABLE IF NOT EXISTS monthly_revenue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        stock_name TEXT,
        year INTEGER,
        month INTEGER,
        revenue REAL,
        revenue_yoy REAL,
        revenue_mom REAL,
        announce_date TEXT,
        is_beat_estimate INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(code, year, month)
    )""")
    print("✓ monthly_revenue")

    # 5. 空頭壓力測試結果
    cur.execute("""CREATE TABLE IF NOT EXISTS bear_market_stress_test (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT,
        test_period TEXT,
        start_date TEXT,
        end_date TEXT,
        strategy_return REAL,
        benchmark_return REAL,
        alpha REAL,
        max_drawdown REAL,
        recovery_days INTEGER,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ bear_market_stress_test")

    conn.commit()
    conn.close()
    print("\n✅ V8 migration 完成")

if __name__ == "__main__":
    migrate()
