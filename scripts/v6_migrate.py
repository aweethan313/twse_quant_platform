"""scripts/v6_migrate.py - V6 資料庫建表"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    print(f"DB: {DB}\n")

    # 1. v6_strategy_backtest_results
    cur.execute("""CREATE TABLE IF NOT EXISTS v6_strategy_backtest_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        start_date TEXT, end_date TEXT,
        total_return REAL, benchmark_0050_return REAL, alpha_vs_0050 REAL,
        annualized_return REAL, max_drawdown REAL, win_rate REAL,
        trade_count INTEGER, profit_factor REAL,
        average_win REAL, average_loss REAL,
        fee_total REAL, tax_total REAL,
        sharpe_ratio REAL, calmar_ratio REAL,
        exposure_ratio REAL, turnover REAL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ v6_strategy_backtest_results")

    # 2. strategy_cooldowns
    cur.execute("""CREATE TABLE IF NOT EXISTS strategy_cooldowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        strategy_name TEXT,
        code TEXT NOT NULL,
        stock_name TEXT,
        triggered_date TEXT NOT NULL,
        stop_loss_price REAL,
        exit_price REAL,
        cooldown_days INTEGER DEFAULT 5,
        cooldown_until TEXT NOT NULL,
        reason TEXT,
        is_active INTEGER DEFAULT 1,
        lifted_date TEXT,
        lifted_reason TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cd_account ON strategy_cooldowns(account_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cd_code ON strategy_cooldowns(code)")
    print("✓ strategy_cooldowns")

    # 3. candidate_forward_returns
    cur.execute("""CREATE TABLE IF NOT EXISTS candidate_forward_returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date TEXT NOT NULL,
        code TEXT NOT NULL,
        stock_name TEXT,
        candidate_score REAL,
        score_bucket TEXT,
        rank INTEGER,
        close_price REAL,
        return_1d REAL, return_3d REAL, return_5d REAL,
        return_10d REAL, return_20d REAL,
        alpha_1d_vs_0050 REAL, alpha_5d_vs_0050 REAL,
        alpha_10d_vs_0050 REAL, alpha_20d_vs_0050 REAL,
        max_runup_20d REAL, max_drawdown_20d REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(signal_date, code)
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cfr_date ON candidate_forward_returns(signal_date)")
    print("✓ candidate_forward_returns")

    # 4. strategy_health_scores
    cur.execute("""CREATE TABLE IF NOT EXISTS strategy_health_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        account_id INTEGER,
        eval_start_date TEXT, eval_end_date TEXT,
        alpha_vs_0050 REAL, max_drawdown REAL,
        win_rate REAL, profit_factor REAL,
        trade_count INTEGER,
        rolling_alpha_20d REAL, rolling_alpha_60d REAL,
        blocked_count INTEGER DEFAULT 0,
        health_score REAL,
        recommendation TEXT,
        reason_summary TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ strategy_health_scores")

    # 5. chip_anomaly_alerts
    cur.execute("""CREATE TABLE IF NOT EXISTS chip_anomaly_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        stock_name TEXT,
        alert_type TEXT NOT NULL,
        investor_type TEXT,
        buy_sell_value REAL,
        buy_sell_volume REAL,
        streak_days INTEGER,
        volume_ratio REAL,
        severity TEXT DEFAULT 'INFO',
        score_impact_suggestion REAL DEFAULT 0,
        reason_summary TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_caa_date ON chip_anomaly_alerts(trade_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_caa_code ON chip_anomaly_alerts(code)")
    print("✓ chip_anomaly_alerts")

    conn.commit()
    conn.close()
    print("\n✅ V6 migration 完成")

if __name__ == "__main__":
    migrate()
