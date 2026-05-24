"""v4_api_patch.py - 套用 V4 APIs 到 main.py"""
with open("main.py") as f:
    c = f.read()

V4_APIS = '''
# ═══════════════════════════════════
# V4 APIs
# ═══════════════════════════════════

@app.get("/api/quality/data")
def api_data_quality(query_date: str = None, limit: int = 50):
    """V4-1 資料品質檢查"""
    from backend.v4.data_quality import run_data_quality_checks, get_quality_report
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    existing = get_quality_report(str(td), limit)
    if not existing:
        result = run_data_quality_checks(td)
        existing = get_quality_report(str(td), limit)
    return {"checks": existing, "count": len(existing)}


@app.get("/api/workflow/daily-runs")
def api_workflow_runs(run_date: str = None, limit: int = 100):
    """V4-3 每日工作流程記錄"""
    from backend.v4.daily_workflow import get_workflow_runs
    return get_workflow_runs(run_date=run_date, limit=limit)


@app.post("/api/workflow/run")
def api_run_workflow(run_date: str = None):
    """V4-3 執行每日工作流程"""
    from backend.v4.daily_workflow import run_daily_workflow
    from datetime import date as ddate
    td = ddate.fromisoformat(run_date) if run_date else ddate.today()
    return run_daily_workflow(td)


@app.get("/api/trade-plan/tomorrow")
def api_tomorrow_trade_plan(query_date: str = None, limit: int = 30):
    """V4-4 明日交易計畫"""
    from backend.v3.candidate_trade_plans import get_trade_plans, generate_daily_plans
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    plans = get_trade_plans(plan_date=str(td), limit=limit)
    if not plans:
        plans = generate_daily_plans(td, limit=limit)
    return plans


@app.get("/api/strategies/kill-switch")
def api_strategy_kill_switch(query_date: str = None):
    """V4-9 策略 Kill Switch 狀態"""
    from backend.v4.strategy_kill_switch import run_kill_switch, get_kill_switch_status
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    status = get_kill_switch_status(str(td))
    if not status:
        status = run_kill_switch(td)
    return status


@app.get("/api/market/classification")
def api_market_classification(
    code: str = None, primary_category: str = None,
    min_heat_score: float = None, limit: int = 200
):
    """V4-13 股市分類"""
    from backend.v4.market_sector import get_classification, build_classification
    from datetime import date as ddate
    result = get_classification(code=code, primary_category=primary_category,
                                min_heat=min_heat_score, limit=limit)
    if not result:
        build_classification(ddate.today())
        result = get_classification(code=code, primary_category=primary_category,
                                    min_heat=min_heat_score, limit=limit)
    return result


@app.get("/api/market/theme-exposure")
def api_theme_exposure(account_id: int = None):
    """V4-13 主題曝險"""
    from backend.v4.market_sector import get_theme_exposure
    return get_theme_exposure(account_id=account_id)


@app.get("/api/market/sector-heat")
def api_sector_heat():
    """V4-13 產業題材熱度"""
    from backend.v4.market_sector import get_classification
    from collections import defaultdict
    data = get_classification(limit=2000)
    heat_by_cat = defaultdict(list)
    for d in data:
        heat_by_cat[d["primary_category"]].append(d["theme_heat_score"] or 50)
    return [
        {"category": cat, "avg_heat": round(sum(v)/len(v), 1), "count": len(v)}
        for cat, v in sorted(heat_by_cat.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
    ]


@app.get("/api/research/backtest-paper-gap")
def api_backtest_paper_gap(strategy_id: int = None, limit: int = 100):
    """V4-5 回測vs實測差距（骨架）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        q = "SELECT * FROM backtest_paper_gap_analysis WHERE 1=1"
        params = {}
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        q += " ORDER BY id DESC LIMIT :limit"; params["limit"] = limit
        rows = db.execute(_text(q), params).fetchall()
        return [dict(zip(
            ["id","analysis_date","strategy_id","account_id","code","signal_date",
             "backtest_expected_return_1d","backtest_expected_return_3d","backtest_expected_return_5d",
             "paper_actual_return_1d","paper_actual_return_3d","paper_actual_return_5d",
             "expected_fill_price","actual_fill_price","fill_price_gap","slippage_gap",
             "missed_trade","risk_blocked","gap_reason","severity","created_at"], r
        )) for r in rows]
    finally:
        db.close()


@app.get("/api/research/strategy-attribution")
def api_strategy_attribution(strategy_id: int = None, limit: int = 100):
    """V4-6 策略獲利歸因（骨架）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_attribution WHERE 1=1"
        params = {}
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        q += " ORDER BY total_pnl DESC LIMIT :limit"; params["limit"] = limit
        rows = db.execute(_text(q), params).fetchall()
        return rows
    finally:
        db.close()


@app.get("/api/portfolio/optimizer")
def api_portfolio_optimizer(account_id: int = None):
    """V4-7 投組配置器（骨架）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    from backend.v4.market_sector import get_theme_exposure
    db = SessionLocal()
    try:
        exposure = get_theme_exposure(account_id)
        equity = db.execute(_text("""
            SELECT total_equity, cash FROM equity_curve
            WHERE (:aid IS NULL OR account_id=:aid)
            ORDER BY snap_date DESC LIMIT 1
        """), {"aid": account_id}).fetchone()
        total = float(equity[0] or 200000) if equity else 200000
        cash = float(equity[1] or 0) if equity else 0
        return {
            "total_capital": total,
            "cash": cash,
            "cash_ratio": round(cash/total*100, 1) if total else 0,
            "theme_exposure": exposure,
            "recommendation": "⚠️ 需確認主題曝險是否過度集中" if exposure else "正常",
            "note": "系統模式：輔助建議，非自動下單",
        }
    finally:
        db.close()


@app.get("/api/intraday/watch")
def api_intraday_watch():
    """V4-8 盤中觀察（無分鐘資料時SKIPPED）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        count = db.execute(_text("SELECT COUNT(*) FROM ohlcv_1min")).scalar() or 0
        if count == 0:
            return {"status": "SKIPPED", "reason": "ohlcv_1min 無資料，盤中觀察跳過",
                    "events": []}
        rows = db.execute(_text("SELECT * FROM intraday_watch_events ORDER BY event_time DESC LIMIT 50")).fetchall()
        return {"status": "OK", "events": rows}
    except:
        return {"status": "SKIPPED", "reason": "ohlcv_1min 資料表不存在", "events": []}
    finally:
        db.close()


@app.get("/api/risk/scenario-stress")
def api_scenario_stress(account_id: int = None):
    """V4-10 情境壓力測試（骨架）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        rows = db.execute(_text("SELECT * FROM scenario_stress_results ORDER BY id DESC LIMIT 20")).fetchall()
        if not rows:
            return {"note": "尚無壓力測試結果，請執行 v4_10_run_scenario_stress_test"}
        return rows
    finally:
        db.close()
'''

TARGET = "# ─── 啟動 ───" if "# ─── 啟動 ───" in c else 'if __name__ == "__main__"'
if "/api/quality/data" not in c:
    c = c.replace(TARGET, V4_APIS + "\n" + TARGET) if TARGET in c else c + "\n" + V4_APIS
    print("✓ V4 APIs 加入")
else:
    print("- V4 APIs 已存在")

with open("main.py", "w") as f:
    f.write(c)

import subprocess
r = subprocess.run(["python3", "-m", "py_compile", "main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode == 0 else "❌ " + r.stderr.decode())
