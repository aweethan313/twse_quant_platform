"""
scripts/v3_migrate.py
V3 資料表 migration（可重複執行）
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

TABLES = {
"decision_explanations": """
CREATE TABLE IF NOT EXISTS decision_explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_time TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    account_id INTEGER,
    strategy_id INTEGER,
    code TEXT NOT NULL,
    name TEXT,
    action TEXT NOT NULL,
    final_score REAL,
    technical_score REAL,
    volume_score REAL,
    fundamental_score REAL,
    valuation_score REAL,
    chip_score REAL,
    news_theme_score REAL,
    market_regime_score REAL,
    technical_reason TEXT,
    volume_reason TEXT,
    fundamental_reason TEXT,
    valuation_reason TEXT,
    chip_reason TEXT,
    news_theme_reason TEXT,
    market_regime_reason TEXT,
    risk_reason TEXT,
    blocked_reason TEXT,
    final_explanation TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"strategy_router_decisions": """
CREATE TABLE IF NOT EXISTS strategy_router_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_time TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    market_trend TEXT,
    tech_trend TEXT,
    semiconductor_trend TEXT,
    ai_theme_strength REAL,
    risk_level TEXT,
    enabled_strategies TEXT,
    disabled_strategies TEXT,
    strategy_weight_json TEXT,
    position_multiplier REAL,
    sector_weight_adjustments_json TEXT,
    theme_weight_adjustments_json TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"risk_budget_status": """
CREATE TABLE IF NOT EXISTS risk_budget_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_time TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    account_id INTEGER,
    code TEXT,
    order_action TEXT,
    requested_order_amount REAL,
    allowed_order_amount REAL,
    adjusted_position_size REAL,
    current_stock_exposure REAL,
    current_theme_exposure_json TEXT,
    current_sector_exposure_json TEXT,
    current_strategy_exposure_json TEXT,
    min_cash_ratio REAL DEFAULT 0.10,
    single_stock_max_ratio REAL DEFAULT 0.15,
    single_theme_max_ratio REAL DEFAULT 0.50,
    single_strategy_max_ratio REAL DEFAULT 0.40,
    high_volatility_stock_max_ratio REAL DEFAULT 0.08,
    risk_level TEXT,
    blocked_reason TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"walk_forward_results": """
CREATE TABLE IF NOT EXISTS walk_forward_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER,
    parameter_set_json TEXT,
    train_start TEXT,
    train_end TEXT,
    test_start TEXT,
    test_end TEXT,
    train_return REAL,
    test_return REAL,
    test_max_drawdown REAL,
    test_win_rate REAL,
    test_profit_factor REAL,
    test_trade_count INTEGER,
    stability_score REAL,
    overfit_score REAL,
    overfit_warning TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"strategy_leaderboard": """
CREATE TABLE IF NOT EXISTS strategy_leaderboard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER,
    account_id INTEGER,
    as_of_date TEXT,
    total_return REAL,
    annualized_return REAL,
    max_drawdown REAL,
    win_rate REAL,
    profit_factor REAL,
    average_holding_days REAL,
    trade_count INTEGER,
    stability_score REAL,
    overfit_score REAL,
    strategy_rank_score REAL,
    risk_label TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"paper_trading_research_log": """
CREATE TABLE IF NOT EXISTS paper_trading_research_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    strategy_id INTEGER,
    account_id INTEGER,
    code TEXT NOT NULL,
    name TEXT,
    suggested_action TEXT,
    suggested_price REAL,
    actual_fill_price REAL,
    reason_at_decision_time TEXT,
    market_regime_at_decision_time TEXT,
    score_components_json TEXT,
    result_1d REAL,
    result_3d REAL,
    result_5d REAL,
    result_10d REAL,
    was_decision_correct INTEGER,
    error_type TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
)""",

"realistic_trade_fills": """
CREATE TABLE IF NOT EXISTS realistic_trade_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    strategy_id INTEGER,
    code TEXT,
    action TEXT,
    signal_time TEXT,
    decision_time TEXT,
    order_time TEXT,
    fill_time TEXT,
    requested_shares REAL,
    filled_shares REAL,
    fill_price REAL,
    reference_price REAL,
    slippage REAL,
    fee REAL,
    tax REAL,
    execution_status TEXT,
    execution_reason TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_de_trade_date ON decision_explanations(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_de_code ON decision_explanations(code)",
    "CREATE INDEX IF NOT EXISTS idx_de_action ON decision_explanations(action)",
    "CREATE INDEX IF NOT EXISTS idx_srd_trade_date ON strategy_router_decisions(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_rbs_trade_date ON risk_budget_status(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_rbs_account ON risk_budget_status(account_id)",
    "CREATE INDEX IF NOT EXISTS idx_sl_as_of ON strategy_leaderboard(as_of_date)",
    "CREATE INDEX IF NOT EXISTS idx_ptrl_date ON paper_trading_research_log(date)",
    "CREATE INDEX IF NOT EXISTS idx_ptrl_code ON paper_trading_research_log(code)",
]

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    created = []
    for name, sql in TABLES.items():
        cur.execute(sql)
        created.append(name)
        print(f"  ✓ {name}")
    for idx in INDEXES:
        cur.execute(idx)
    conn.commit()
    conn.close()
    print(f"\n✓ V3 migration 完成，共 {len(created)} 個資料表")

if __name__ == "__main__":
    print(f"DB: {DB}")
    migrate()
