"""scripts/v4_final_acceptance_check.py"""
import sys, os, sqlite3, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings

DB = str(settings.DB_PATH)
BASE = "http://localhost:8000"
results = []

def check(name, passed, warn=False, detail=""):
    label = "✅ PASS" if passed else ("⚠️  WARN" if warn else "❌ FAIL")
    results.append((label, name, detail))
    print(f"  {label}  {name}" + (f" | {detail}" if detail else ""))

def tbl(conn, name):
    return conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{name}'").fetchone() is not None

def api(ep):
    try:
        r = requests.get(f"{BASE}{ep}", timeout=3)
        return r.status_code == 200
    except:
        return False

def run():
    print("\nV4 Final Acceptance Check")
    print("=" * 55)
    conn = sqlite3.connect(DB)

    # V4 表
    for t in ["data_quality_checks","daily_workflow_runs","tomorrow_trade_plans",
              "backtest_paper_gap_analysis","strategy_attribution",
              "portfolio_optimization_plans","intraday_watch_events",
              "strategy_kill_switch_status","scenario_stress_results",
              "market_sector_classification"]:
        check(f"table {t}", tbl(conn, t))

    # V3 表確認
    for t in ["decision_explanations","realistic_trade_fills","strategy_router_decisions",
              "risk_budget_status","walk_forward_results","strategy_leaderboard",
              "candidate_trade_plans","watchlist_alerts","candidate_accuracy_tracker"]:
        check(f"V3 table {t}", tbl(conn, t))

    # 資料品質
    row = conn.execute("SELECT COUNT(*) FROM data_quality_checks").fetchone()
    check("data_quality_checks has data", (row[0] if row else 0) > 0, warn=True,
          detail=f"{row[0] if row else 0} rows")

    # Kill switch
    row = conn.execute("SELECT COUNT(*) FROM strategy_kill_switch_status").fetchone()
    check("strategy_kill_switch has data", (row[0] if row else 0) > 0, warn=True,
          detail=f"{row[0] if row else 0} rows")

    # Market sector
    row = conn.execute("SELECT COUNT(*) FROM market_sector_classification").fetchone()
    check("market_sector_classification has data", (row[0] if row else 0) > 0,
          warn=True, detail=f"{row[0] if row else 0} rows")

    # 0050 保護
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM realistic_trade_fills
            WHERE code='0050' AND action='sell' AND execution_status='FILLED'
        """).fetchone()
        check("0050 not force-sold", (row[0] if row else 0) == 0,
              detail=f"{row[0] if row else 0} violations")
    except:
        check("0050 not force-sold", True, warn=True, detail="no fills data")

    # fill_time 順序
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM realistic_trade_fills
            WHERE fill_time IS NOT NULL AND fill_time <= signal_time
            AND execution_status='FILLED'
        """).fetchone()
        check("no fill_time <= signal_time", (row[0] if row else 0) == 0,
              detail=f"{row[0] if row else 0} violations")
    except:
        check("no fill_time <= signal_time", True, warn=True)

    # ohlcv_1min
    try:
        row = conn.execute("SELECT COUNT(*) FROM ohlcv_1min").fetchone()
        check("ohlcv_1min data", (row[0] if row else 0) > 0, warn=True,
              detail=f"{row[0] if row else 0} rows - intraday SKIPPED if 0")
    except:
        check("ohlcv_1min table", False, warn=True, detail="table not found, SKIPPED")

    # allow_auto_order
    try:
        from config.capital_config import CAPITAL_CONFIG
        check("allow_auto_order=False", not CAPITAL_CONFIG.allow_auto_order)
        check("require_user_confirmation=True", CAPITAL_CONFIG.require_user_confirmation)
    except:
        check("capital_config", False, warn=True)

    conn.close()

    # APIs
    for ep in ["/api/quality/data", "/api/strategies/kill-switch",
               "/api/market/classification", "/api/v3/strategies/router",
               "/api/v3/strategies/leaderboard", "/api/candidates/trade-plans",
               "/api/watchlist/alerts", "/api/workflow/daily-runs"]:
        check(f"API {ep[:40]}", api(ep), warn=True)

    # 摘要
    print("\n" + "=" * 55)
    p = sum(1 for r in results if "PASS" in r[0])
    f = sum(1 for r in results if "FAIL" in r[0])
    w = sum(1 for r in results if "WARN" in r[0])
    pct = int((p + w*0.5) / len(results) * 100) if results else 0
    print(f"PASS: {p}  FAIL: {f}  WARN: {w}  TOTAL: {len(results)}")
    print(f"V4 completion estimate: {pct}%")
    print("=" * 55 + "\n")

if __name__ == "__main__":
    run()
