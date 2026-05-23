"""v3c_api_patch.py"""
with open("main.py") as f:
    c = f.read()

V3C_APIS = '''
# ═══════════════════════════════════
# V3c APIs (FIX-4, FIX-5, FIX-8)
# ═══════════════════════════════════

@app.get("/v3", response_class=HTMLResponse)
def page_v3_dashboard(request: Request):
    """V3-FIX-8 V3 系統總覽頁面"""
    return templates.TemplateResponse("v3_dashboard.html", {"request": request})


@app.get("/api/backtest/realistic/results")
def api_realistic_fills(
    account_id: int = None, code: str = None,
    start_date: str = None, limit: int = 100
):
    """V3-FIX-4 真實成交記錄"""
    from backend.v3.realistic_trade_fills import get_fills
    return get_fills(account_id=account_id, code=code,
                     start_date=start_date, limit=limit)


@app.post("/api/backtest/realistic/fill")
def api_process_fill(
    account_id: int, strategy_id: int, code: str, action: str,
    signal_date: str, requested_shares: float,
    signal_price: float = None, is_fractional: bool = False
):
    """V3-FIX-4 處理單筆成交請求"""
    from backend.v3.realistic_trade_fills import process_fill
    from datetime import date as ddate
    sd = ddate.fromisoformat(signal_date)
    return process_fill(account_id=account_id, strategy_id=strategy_id,
                        code=code, action=action, signal_date=sd,
                        requested_shares=requested_shares,
                        signal_price=signal_price, is_fractional=is_fractional)


@app.get("/api/backtest/walk-forward")
def api_walk_forward_results(strategy_id: int = None, limit: int = 200):
    """V3-FIX-5 Walk-forward 結果"""
    from backend.v3.walk_forward_validator import get_walk_forward_results
    return get_walk_forward_results(strategy_id=strategy_id, limit=limit)


@app.post("/api/backtest/walk-forward/run")
def api_run_walk_forward(
    strategy_id: int = None,
    data_start: str = "2025-02-01",
    data_end: str = None
):
    """V3-FIX-5 執行 Walk-forward 驗證"""
    from backend.v3.walk_forward_validator import run_walk_forward, run_all_strategies_walk_forward
    from datetime import date as ddate
    start = ddate.fromisoformat(data_start)
    end   = ddate.fromisoformat(data_end) if data_end else ddate.today()
    if strategy_id:
        results = run_walk_forward(strategy_id, start, end)
        return results
    else:
        return run_all_strategies_walk_forward(start, end)
'''

TARGET = "# ─── 啟動 ───" if "# ─── 啟動 ───" in c else 'if __name__ == "__main__"'
if "/api/backtest/realistic/results" not in c:
    if TARGET in c:
        c = c.replace(TARGET, V3C_APIS + "\n" + TARGET)
    else:
        c += "\n" + V3C_APIS
    print("✓ V3c APIs 加入")
else:
    print("- V3c APIs 已存在")

# 加導覽列
with open("frontend/templates/base.html") as f:
    b = f.read()
old_nav = '<a href="/candidates" class="nav-link px-3 py-1.5 rounded text-sm {% block nav_candidates %}text-gray-400 hover:text-white{% endblock %}">今日候選</a>'
new_nav = old_nav + '\n    <a href="/v3" class="nav-link px-3 py-1.5 rounded text-sm {% block nav_v3 %}text-gray-400 hover:text-white{% endblock %}">V3 總覽</a>'
if '/v3' not in b:
    b = b.replace(old_nav, new_nav)
    print("✓ 導覽列加入 V3 總覽")
with open("frontend/templates/base.html","w") as f:
    f.write(b)

with open("main.py","w") as f:
    f.write(c)

import subprocess
r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌: "+r.stderr.decode())
