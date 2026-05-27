"""scripts/v7_migrate.py - V7 資料庫建表"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from config.settings import settings

DB = str(settings.DB_PATH)

def migrate():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    print(f"DB: {DB}\n")

    # 1. market_timing_signals - 大盤擇時
    cur.execute("""CREATE TABLE IF NOT EXISTS market_timing_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL UNIQUE,
        market_index TEXT DEFAULT '加權指數',
        close REAL, ma20 REAL, ma60 REAL,
        above_ma20 INTEGER, above_ma60 INTEGER,
        risk_level TEXT DEFAULT 'medium',
        position_multiplier REAL DEFAULT 1.0,
        breadth_score REAL,
        reason_summary TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mts_date ON market_timing_signals(trade_date)")
    print("✓ market_timing_signals")

    # 2. stock_event_calendar - 財報/月營收事件
    cur.execute("""CREATE TABLE IF NOT EXISTS stock_event_calendar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        stock_name TEXT,
        event_type TEXT NOT NULL,
        event_date TEXT NOT NULL,
        announcement_time TEXT,
        source TEXT,
        title TEXT,
        summary TEXT,
        revenue_value REAL,
        revenue_yoy REAL,
        is_confirmed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(code, event_type, event_date)
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sec_code ON stock_event_calendar(code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sec_date ON stock_event_calendar(event_date)")
    print("✓ stock_event_calendar")

    # 3. event_return_analysis - 事件後報酬追蹤
    cur.execute("""CREATE TABLE IF NOT EXISTS event_return_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER,
        code TEXT NOT NULL,
        event_type TEXT,
        event_date TEXT,
        close_before REAL,
        return_1d REAL, return_3d REAL, return_5d REAL, return_10d REAL,
        alpha_5d_vs_0050 REAL,
        volume_change REAL,
        gap_pct REAL,
        conclusion TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ event_return_analysis")

    # 4. sector_theme_rotation - 產業輪動
    cur.execute("""CREATE TABLE IF NOT EXISTS sector_theme_rotation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        theme_name TEXT NOT NULL,
        stock_count INTEGER,
        avg_return_1d REAL, avg_return_5d REAL, avg_return_20d REAL,
        avg_volume_ratio REAL,
        chip_strength REAL,
        theme_strength_score REAL,
        rank INTEGER,
        momentum_5d REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(trade_date, theme_name)
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_str_date ON sector_theme_rotation(trade_date)")
    print("✓ sector_theme_rotation")

    # 5. factor_analysis_results - 多因子分析
    cur.execute("""CREATE TABLE IF NOT EXISTS factor_analysis_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_date TEXT,
        factor_name TEXT,
        ic_mean REAL,
        ic_std REAL,
        icir REAL,
        hit_rate REAL,
        avg_return_5d REAL,
        avg_return_10d REAL,
        suggested_weight REAL,
        current_weight REAL,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    print("✓ factor_analysis_results")

    # 6. us_market_events - 美股事件
    cur.execute("""CREATE TABLE IF NOT EXISTS us_market_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        event_type TEXT,
        change_pct REAL,
        tw_impact_5d REAL,
        tw_semi_impact_5d REAL,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(event_date, ticker, event_type)
    )""")
    print("✓ us_market_events")

    conn.commit()
    conn.close()
    print("\n✅ V7 migration 完成")

if __name__ == "__main__":
    migrate()
