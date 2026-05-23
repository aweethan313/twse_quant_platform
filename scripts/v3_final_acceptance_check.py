"""
scripts/v3_final_acceptance_check.py
V3 最終驗收腳本
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
import requests
from datetime import date
from config.settings import settings

DB = str(settings.DB_PATH)
BASE_URL = "http://localhost:8000"

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"

results = []


def check(name, passed, warn=False, detail=""):
    label = WARN if (warn and not passed) else (PASS if passed else FAIL)
    results.append((label, name, detail))
    print(f"  {label}  {name}" + (f" | {detail}" if detail else ""))


def table_exists(conn, table):
    cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
    return cur.fetchone() is not None


def run():
    print("\nV3 Final Acceptance Check")
    print("=" * 55)
    conn = sqlite3.connect(DB)

    # 1. 資料表存在
    v3_tables = [
        "decision_explanations",
        "strategy_router_decisions",
        "risk_budget_status",
        "realistic_trade_fills",
        "walk_forward_results",
        "strategy_leaderboard",
        "paper_trading_research_log",
    ]
    for t in v3_tables:
        exists = table_exists(conn, t)
        check(f"table {t}", exists)

    # 2. decision_explanations 有 BUY / SELL / HOLD
    for action in ["BUY", "SELL", "HOLD"]:
        row = conn.execute(f"SELECT COUNT(*) FROM decision_explanations WHERE action='{action}'").fetchone()
        count = row[0] if row else 0
        check(f"decision_explanations has {action}", count > 0,
              warn=(action == "HOLD"), detail=f"{count} rows")

    # 3. blocked_reason 有記錄
    row = conn.execute("SELECT COUNT(*) FROM decision_explanations WHERE blocked_reason IS NOT NULL").fetchone()
    check("blocked_reason recorded", (row[0] if row else 0) > 0)

    # 4. strategy_router_decisions 有記錄
    row = conn.execute("SELECT COUNT(*) FROM strategy_router_decisions").fetchone()
    check("strategy_router has decisions", (row[0] if row else 0) > 0,
          detail=f"{row[0] if row else 0} rows")

    # 5. risk_budget_status 有記錄
    row = conn.execute("SELECT COUNT(*) FROM risk_budget_status").fetchone()
    check("risk_budget_status has records", (row[0] if row else 0) > 0,
          warn=True, detail=f"{row[0] if row else 0} rows")

    # 6. strategy_leaderboard 有記錄
    row = conn.execute("SELECT COUNT(*) FROM strategy_leaderboard").fetchone()
    check("strategy_leaderboard has records", (row[0] if row else 0) > 0,
          warn=True, detail=f"{row[0] if row else 0} rows")

    # 7. paper_trading_research_log 存在
    exists = table_exists(conn, "paper_trading_research_log")
    check("paper_trading_research_log exists", exists)

    # 8. 現有資料表不被破壞
    for t in ["ohlcv_daily", "daily_scores", "trade_logs", "equity_curve", "strategy_accounts"]:
        check(f"existing table {t} intact", table_exists(conn, t))

    # 9. daily_scores 有 S8 欄位
    row = conn.execute("PRAGMA table_info(daily_scores)").fetchall()
    cols = {r[1] for r in row}
    s8_cols = ["candidate_score","entry_score","risk_score","final_score","final_action","stock_class"]
    for c in s8_cols:
        check(f"daily_scores.{c} exists", c in cols)

    # 10. API 健康檢查
    try:
        r = requests.get(f"{BASE_URL}/api/market/overview", timeout=3)
        check("API /api/market/overview", r.status_code == 200)
    except Exception as e:
        check("API /api/market/overview", False, detail=f"server not running: {e}")

    api_endpoints = [
        "/api/decisions/explanations",
        "/api/strategies/router",
        "/api/risk/budget",
        "/api/strategies/leaderboard",
        "/api/paper/research-log",
        "/api/stocks/rankings?rank_mode=final&limit=5",
    ]
    for ep in api_endpoints:
        try:
            r = requests.get(f"{BASE_URL}{ep}", timeout=3)
            check(f"API {ep[:40]}", r.status_code == 200)
        except Exception as e:
            check(f"API {ep[:40]}", False, warn=True, detail="server not running")

    # 11. 0050 不會被短線策略強制賣出（檢查 decision_explanations）
    row = conn.execute("""
        SELECT COUNT(*) FROM decision_explanations
        WHERE code='0050' AND action='SELL'
    """).fetchone()
    etf_sells = row[0] if row else 0
    check("0050 not force-sold", etf_sells == 0, warn=True,
          detail=f"{etf_sells} SELL records for 0050")

    # 12. 1分鐘K資料
    row = conn.execute("SELECT COUNT(*) FROM ohlcv_1min").fetchone() if table_exists(conn, "ohlcv_1min") else None
    if row:
        check("ohlcv_1min has data", row[0] > 0, warn=True, detail=f"{row[0]} rows")
    else:
        check("ohlcv_1min table", False, warn=True, detail="table not found, intraday validation skipped")

    conn.close()

    # 摘要
    print("\n" + "=" * 55)
    passed  = sum(1 for r in results if r[0] == PASS)
    failed  = sum(1 for r in results if r[0] == FAIL)
    warned  = sum(1 for r in results if r[0] == WARN)
    total   = len(results)
    pct     = int((passed + warned * 0.5) / total * 100) if total > 0 else 0

    print(f"PASS: {passed}  FAIL: {failed}  WARN: {warned}  TOTAL: {total}")
    print(f"V3 completion estimate: {pct}%")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    run()
