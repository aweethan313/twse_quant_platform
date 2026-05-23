"""scripts/v3b_migrate.py - 新增 V3-FIX-10~15 資料表"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

TABLES = {
"candidate_trade_plans": """
CREATE TABLE IF NOT EXISTS candidate_trade_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    candidate_pool_type TEXT,
    entry_price_low REAL,
    entry_price_high REAL,
    reference_price REAL,
    target_price_1 REAL,
    target_price_2 REAL,
    stop_loss_price REAL,
    expected_return_1 REAL,
    expected_return_2 REAL,
    downside_risk REAL,
    risk_reward_ratio REAL,
    suggested_shares INTEGER,
    suggested_amount REAL,
    max_loss_amount REAL,
    position_size_reason TEXT,
    invalid_buy_condition TEXT,
    final_plan_summary TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"watchlist_alerts": """
CREATE TABLE IF NOT EXISTS watchlist_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date TEXT NOT NULL,
    alert_time TEXT,
    code TEXT NOT NULL,
    name TEXT,
    alert_type TEXT,
    entry_price_low REAL,
    entry_price_high REAL,
    target_price_1 REAL,
    target_price_2 REAL,
    stop_loss_price REAL,
    risk_reward_ratio REAL,
    suggested_shares INTEGER,
    suggested_amount REAL,
    alert_reason TEXT,
    warning_message TEXT,
    delivery_channel TEXT DEFAULT 'local_report',
    delivery_status TEXT DEFAULT 'PENDING',
    user_confirmation_status TEXT DEFAULT 'PENDING',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"candidate_accuracy_tracker": """
CREATE TABLE IF NOT EXISTS candidate_accuracy_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    strategy_id INTEGER,
    candidate_pool_type TEXT,
    reference_price REAL,
    entry_price_low REAL,
    entry_price_high REAL,
    target_price_1 REAL,
    target_price_2 REAL,
    stop_loss_price REAL,
    max_return_1d REAL,  max_return_3d REAL,  max_return_5d REAL,
    max_return_10d REAL, max_return_20d REAL,
    min_return_1d REAL,  min_return_3d REAL,  min_return_5d REAL,
    min_return_10d REAL, min_return_20d REAL,
    hit_target_1_5d INTEGER, hit_target_1_10d INTEGER,
    hit_target_2_10d INTEGER,
    hit_stop_loss_5d INTEGER, hit_stop_loss_10d INTEGER,
    result_label TEXT,
    error_type TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(signal_date, code, strategy_id)
)""",

"candidate_news": """
CREATE TABLE IF NOT EXISTS candidate_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_time TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    title TEXT,
    source TEXT,
    source_credibility_score REAL DEFAULT 50,
    sentiment TEXT DEFAULT 'neutral',
    related_themes TEXT,
    is_official_disclosure INTEGER DEFAULT 0,
    is_financial_report INTEGER DEFAULT 0,
    is_monthly_revenue INTEGER DEFAULT 0,
    is_investor_conference INTEGER DEFAULT 0,
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ctp_date ON candidate_trade_plans(plan_date)",
    "CREATE INDEX IF NOT EXISTS idx_wa_date ON watchlist_alerts(alert_date)",
    "CREATE INDEX IF NOT EXISTS idx_cat_signal ON candidate_accuracy_tracker(signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_cat_code ON candidate_accuracy_tracker(code)",
    "CREATE INDEX IF NOT EXISTS idx_cn_code ON candidate_news(code)",
]

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    for name, sql in TABLES.items():
        cur.execute(sql)
        print(f"  ✓ {name}")
    for idx in INDEXES:
        cur.execute(idx)
    conn.commit()
    conn.close()
    print(f"\n✓ V3b migration 完成")

if __name__ == "__main__":
    print(f"DB: {DB}")
    migrate()
