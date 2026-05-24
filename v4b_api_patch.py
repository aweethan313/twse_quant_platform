"""v4b_api_patch.py"""
with open("main.py") as f:
    c = f.read()

V4B_APIS = '''
# ═══════════════════════════════════
# V4b APIs (Factor Store, Research, Stress Test)
# ═══════════════════════════════════

@app.get("/api/factors/store")
def api_factor_store(
    code: str = None, query_date: str = None,
    factor_group: str = None, factor_name: str = None,
    decision_time: str = None, limit: int = 200
):
    """V4-2 Factor Store"""
    from backend.v4.factor_store import get_factors, build_factor_store
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    result = get_factors(code=code, factor_date=str(td),
                         factor_group=factor_group, factor_name=factor_name,
                         decision_time=decision_time, limit=limit)
    if not result and not code:
        build_factor_store(td)
        result = get_factors(code=code, factor_date=str(td),
                             factor_group=factor_group, factor_name=factor_name,
                             decision_time=decision_time, limit=limit)
    return result


@app.get("/api/research/backtest-paper-gap")
def api_backtest_paper_gap_v2(strategy_id: int = None, limit: int = 100):
    """V4-5 回測vs實測差距"""
    from backend.v4.research import get_gap_analysis, analyze_backtest_paper_gap
    from datetime import date as ddate
    result = get_gap_analysis(strategy_id=strategy_id, limit=limit)
    if not result:
        analyze_backtest_paper_gap(strategy_id=strategy_id, analysis_date=ddate.today())
        result = get_gap_analysis(strategy_id=strategy_id, limit=limit)
    return result


@app.get("/api/research/strategy-attribution")
def api_strategy_attribution_v2(strategy_id: int = None, limit: int = 100):
    """V4-6 策略獲利歸因"""
    from backend.v4.research import run_strategy_attribution
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    from datetime import date as ddate
    db = SessionLocal()
    try:
        rows = db.execute(_text(
            "SELECT * FROM strategy_attribution" +
            (" WHERE strategy_id=:sid" if strategy_id else "") +
            " ORDER BY total_pnl DESC LIMIT :limit"
        ), {"sid": strategy_id, "limit": limit} if strategy_id else {"limit": limit}).fetchall()
        if not rows:
            run_strategy_attribution(strategy_id=strategy_id, analysis_date=ddate.today())
            rows = db.execute(_text(
                "SELECT * FROM strategy_attribution ORDER BY total_pnl DESC LIMIT :limit"
            ), {"limit": limit}).fetchall()
        cols = ["id","analysis_date","strategy_id","account_id","attribution_type",
                "attribution_key","realized_pnl","unrealized_pnl","total_pnl",
                "pnl_contribution_pct","trade_count","win_rate","avg_return",
                "max_drawdown","concentration_warning","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/portfolio/optimizer")
def api_portfolio_optimizer_v2(account_id: int = None):
    """V4-7 投組配置器"""
    from backend.v4.research import run_portfolio_optimizer
    from datetime import date as ddate
    return run_portfolio_optimizer(account_id=account_id, plan_date=ddate.today())


@app.get("/api/risk/scenario-stress")
def api_scenario_stress_v2(account_id: int = None, test_date: str = None):
    """V4-10 情境壓力測試"""
    from backend.v4.research import get_stress_results, run_scenario_stress_test
    from datetime import date as ddate
    td = ddate.fromisoformat(test_date) if test_date else ddate.today()
    result = get_stress_results(str(td))
    if not result:
        run_scenario_stress_test(account_id=account_id, test_date=td)
        result = get_stress_results(str(td))
    return result


@app.get("/api/reports/research")
def api_research_report(report_date: str = None):
    """V4-11 研究報告"""
    from backend.v4.research_report import export_research_report
    from datetime import date as ddate
    td = ddate.fromisoformat(report_date) if report_date else ddate.today()
    path = export_research_report(td)
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "content": content, "date": str(td)}
    except:
        return {"path": path, "error": "報告尚未生成"}
'''

TARGET = "# ─── 啟動 ───" if "# ─── 啟動 ───" in c else 'if __name__ == "__main__"'
if "/api/factors/store" not in c:
    c = c.replace(TARGET, V4B_APIS + "\n" + TARGET) if TARGET in c else c + "\n" + V4B_APIS
    print("✓ V4b APIs 加入")
else:
    # 替換舊版
    import re
    old_gap = re.search(r"@app\.get\(\"/api/research/backtest-paper-gap\"\).*?(?=\n@app)", c, re.DOTALL)
    if old_gap:
        c = c[:old_gap.start()] + c[old_gap.end():]
        print("✓ 舊版 backtest-paper-gap 移除")
    print("- V4b APIs 部分已存在，更新中")
    c = c + "\n" + V4B_APIS

with open("main.py", "w") as f:
    f.write(c)

import subprocess
r = subprocess.run(["python3", "-m", "py_compile", "main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode == 0 else "❌ " + r.stderr.decode())
