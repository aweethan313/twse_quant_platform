"""scripts/v4_migrate.py - V4 資料表建立（可重複執行）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

TABLES = {
"data_quality_checks": """
CREATE TABLE IF NOT EXISTS data_quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_date TEXT NOT NULL,
    check_time TEXT NOT NULL,
    check_type TEXT NOT NULL,
    data_source TEXT,
    table_name TEXT,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    affected_codes_json TEXT,
    issue_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    health_score REAL DEFAULT 100,
    message TEXT,
    suggestion TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"factor_store": """
CREATE TABLE IF NOT EXISTS factor_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_date TEXT NOT NULL,
    available_at TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    factor_name TEXT NOT NULL,
    factor_value REAL,
    factor_group TEXT,
    source_table TEXT,
    source_time TEXT,
    confidence_score REAL DEFAULT 100,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(factor_date, code, factor_name)
)""",
"daily_workflow_runs": """
CREATE TABLE IF NOT EXISTS daily_workflow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    run_time TEXT NOT NULL,
    workflow_version TEXT DEFAULT 'V4',
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    output_path TEXT,
    duration_seconds REAL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"tomorrow_trade_plans": """
CREATE TABLE IF NOT EXISTS tomorrow_trade_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    target_trade_date TEXT,
    account_id INTEGER,
    strategy_id INTEGER,
    code TEXT NOT NULL,
    name TEXT,
    action_plan TEXT,
    priority TEXT DEFAULT 'MEDIUM',
    reference_price REAL,
    entry_price_low REAL,
    entry_price_high REAL,
    target_price_1 REAL,
    target_price_2 REAL,
    stop_loss_price REAL,
    risk_reward_ratio REAL,
    suggested_shares INTEGER,
    suggested_amount REAL,
    max_loss_amount REAL,
    market_regime TEXT,
    risk_level TEXT,
    candidate_pool_type TEXT,
    reason TEXT,
    invalid_buy_condition TEXT,
    do_not_chase_condition TEXT,
    watch_condition TEXT,
    confirmation_required INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"backtest_paper_gap_analysis": """
CREATE TABLE IF NOT EXISTS backtest_paper_gap_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date TEXT NOT NULL,
    strategy_id INTEGER,
    account_id INTEGER,
    code TEXT,
    signal_date TEXT,
    backtest_expected_return_1d REAL,
    backtest_expected_return_3d REAL,
    backtest_expected_return_5d REAL,
    paper_actual_return_1d REAL,
    paper_actual_return_3d REAL,
    paper_actual_return_5d REAL,
    expected_fill_price REAL,
    actual_fill_price REAL,
    fill_price_gap REAL,
    slippage_gap REAL,
    missed_trade INTEGER DEFAULT 0,
    risk_blocked INTEGER DEFAULT 0,
    gap_reason TEXT,
    severity TEXT DEFAULT 'LOW',
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"strategy_attribution": """
CREATE TABLE IF NOT EXISTS strategy_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date TEXT NOT NULL,
    strategy_id INTEGER,
    account_id INTEGER,
    attribution_type TEXT,
    attribution_key TEXT,
    realized_pnl REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    pnl_contribution_pct REAL DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    avg_return REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    concentration_warning INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"portfolio_optimization_plans": """
CREATE TABLE IF NOT EXISTS portfolio_optimization_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    account_id INTEGER,
    total_capital REAL,
    core_etf_value REAL,
    active_trading_value REAL,
    cash REAL,
    target_core_etf_ratio REAL DEFAULT 0.50,
    target_active_trading_ratio REAL DEFAULT 0.50,
    current_core_etf_ratio REAL,
    current_active_trading_ratio REAL,
    suggested_cash_ratio REAL,
    current_theme_exposure_json TEXT,
    target_theme_exposure_json TEXT,
    current_sector_exposure_json TEXT,
    target_sector_exposure_json TEXT,
    rebalance_action_json TEXT,
    risk_level TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"intraday_watch_events": """
CREATE TABLE IF NOT EXISTS intraday_watch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    event_type TEXT,
    current_price REAL,
    reference_price REAL,
    entry_price_low REAL,
    entry_price_high REAL,
    target_price_1 REAL,
    stop_loss_price REAL,
    volume_status TEXT,
    price_status TEXT,
    alert_message TEXT,
    action_suggestion TEXT,
    confirmation_required INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"strategy_kill_switch_status": """
CREATE TABLE IF NOT EXISTS strategy_kill_switch_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_date TEXT NOT NULL,
    strategy_id INTEGER,
    account_id INTEGER,
    status TEXT DEFAULT 'ACTIVE',
    previous_weight REAL DEFAULT 1.0,
    new_weight REAL DEFAULT 1.0,
    reason TEXT,
    recent_return REAL,
    recent_win_rate REAL,
    recent_max_drawdown REAL,
    backtest_paper_gap REAL,
    trade_count INTEGER,
    overfit_score REAL,
    action_required TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"scenario_stress_results": """
CREATE TABLE IF NOT EXISTS scenario_stress_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_date TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    account_id INTEGER,
    estimated_pnl REAL,
    estimated_return REAL,
    affected_positions_json TEXT,
    max_loss_position TEXT,
    theme_exposure_impact_json TEXT,
    risk_warning TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
"market_sector_classification": """
CREATE TABLE IF NOT EXISTS market_sector_classification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT,
    primary_category TEXT,
    secondary_category TEXT,
    theme_tags_json TEXT,
    industry_group TEXT,
    risk_type TEXT DEFAULT 'NORMAL',
    is_core_watchlist INTEGER DEFAULT 0,
    is_core_etf INTEGER DEFAULT 0,
    is_defensive INTEGER DEFAULT 0,
    theme_heat_score REAL DEFAULT 50,
    classification_confidence REAL DEFAULT 80,
    classification_reason TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    created_at TEXT DEFAULT (datetime('now','localtime'))
)""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dqc_date ON data_quality_checks(check_date)",
    "CREATE INDEX IF NOT EXISTS idx_fs_code_date ON factor_store(code, factor_date)",
    "CREATE INDEX IF NOT EXISTS idx_fs_available ON factor_store(available_at)",
    "CREATE INDEX IF NOT EXISTS idx_ttp_date ON tomorrow_trade_plans(plan_date)",
    "CREATE INDEX IF NOT EXISTS idx_ttp_code ON tomorrow_trade_plans(code)",
    "CREATE INDEX IF NOT EXISTS idx_sa_strategy ON strategy_attribution(strategy_id, analysis_date)",
    "CREATE INDEX IF NOT EXISTS idx_ks_strategy ON strategy_kill_switch_status(strategy_id, check_date)",
    "CREATE INDEX IF NOT EXISTS idx_msc_code ON market_sector_classification(code)",
    "CREATE INDEX IF NOT EXISTS idx_msc_cat ON market_sector_classification(primary_category)",
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
    print(f"\n✓ V4 migration 完成，{len(TABLES)} 個資料表")

if __name__ == "__main__":
    print(f"DB: {DB}")
    migrate()
