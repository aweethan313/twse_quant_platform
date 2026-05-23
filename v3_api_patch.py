"""
V3 API patch for main.py
在 main.py 末尾（if __name__ == "__main__" 之前）加入以下 API

使用方式：
python3 - << 'EOF'
exec(open('v3_api_patch.py').read())
EOF
"""
import ast, re

with open("main.py") as f:
    c = f.read()

V3_APIS = '''
# ═══════════════════════════════════
# V3 APIs
# ═══════════════════════════════════

@app.get("/api/decisions/explanations")
def api_decisions_explanations(
    date: str = None, account_id: int = None, strategy_id: int = None,
    code: str = None, action: str = None, limit: int = 100
):
    """V3-FIX-1 決策理由查詢"""
    from backend.v3.decision_explanations import query_explanations
    return query_explanations(
        trade_date=date, account_id=account_id, strategy_id=strategy_id,
        code=code, action=action, limit=limit
    )


@app.get("/api/strategies/router")
def api_strategies_router(date: str = None):
    """V3-FIX-2 策略路由器狀態"""
    from backend.v3.strategy_router import get_latest_router, compute_router
    from datetime import date as ddate
    td = ddate.fromisoformat(date) if date else ddate.today()
    result = get_latest_router(td)
    if not result or "market_trend" not in result:
        result = compute_router(td)
    return result


@app.get("/api/risk/budget")
def api_risk_budget(account_id: int = None, date: str = None):
    """V3-FIX-3 風險預算狀態"""
    from backend.v3.risk_budget_manager import get_budget_status
    return get_budget_status(account_id=account_id, trade_date=date)


@app.get("/api/strategies/leaderboard")
def api_strategies_leaderboard(date: str = None):
    """V3-FIX-6 策略排名"""
    from backend.v3.strategy_leaderboard import get_leaderboard, compute_leaderboard
    from datetime import date as ddate
    result = get_leaderboard(as_of_date=date)
    if not result:
        td = ddate.fromisoformat(date) if date else ddate.today()
        result = compute_leaderboard(td)
    return result


@app.get("/api/paper/research-log")
def api_paper_research_log(
    code: str = None, strategy_id: int = None,
    date_from: str = None, date_to: str = None, limit: int = 100
):
    """V3-FIX-7 Paper Trading Research Log"""
    from backend.v3.strategy_leaderboard import get_research_log, get_research_summary
    logs = get_research_log(code=code, strategy_id=strategy_id,
                            date_from=date_from, date_to=date_to, limit=limit)
    summary = get_research_summary(strategy_id=strategy_id)
    return {"logs": logs, "summary": summary}


@app.get("/candidates", response_class=HTMLResponse)
def page_candidates(request: Request):
    return templates.TemplateResponse("candidates.html", {"request": request})
'''

TARGET = "# ─── 啟動 ───" if "# ─── 啟動 ───" in c else 'if __name__ == "__main__"'

if "V3 APIs" not in c:
    if TARGET in c:
        c = c.replace(TARGET, V3_APIS + "\n" + TARGET)
    else:
        c = c + "\n" + V3_APIS
    print("✓ V3 APIs 加入")
else:
    print("- V3 APIs 已存在")

with open("main.py", "w") as f:
    f.write(c)

import subprocess
result = subprocess.run(["python3", "-m", "py_compile", "main.py"], capture_output=True)
if result.returncode == 0:
    print("✓ main.py 語法正確")
else:
    print("❌ 語法錯誤:", result.stderr.decode())
