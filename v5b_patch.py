"""v5b_patch.py - V5B 完整 patch"""
import subprocess, re

# ── 1. main.py 加入 V5B APIs ──
with open("main.py") as f:
    c = f.read()

V5B_APIS = '''
# ═══════════════════════════════════════
# V5B APIs
# ═══════════════════════════════════════

@app.post("/api/paper/simulate-fills")
def api_simulate_fills(execution_date: str = None):
    """模擬 T+1 成交"""
    from backend.v5.paper_engine import simulate_paper_fills
    from datetime import date as ddate
    ed = ddate.fromisoformat(execution_date) if execution_date else ddate.today()
    return simulate_paper_fills(ed)


@app.post("/api/paper/update-equity")
def api_update_v5_equity(snap_date: str = None):
    """更新 V5 equity"""
    from backend.v5.paper_engine import update_v5_equity
    from datetime import date as ddate
    sd = ddate.fromisoformat(snap_date) if snap_date else ddate.today()
    return update_v5_equity(sd)


@app.get("/api/paper/fills")
def api_paper_fills(account_id: int = None, limit: int = 30):
    """成交記錄"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = "SELECT * FROM paper_fills WHERE 1=1"
        params = {}
        if account_id: q += " AND account_id=:id"; params["id"] = account_id
        q += " ORDER BY id DESC LIMIT :n"; params["n"] = limit
        rows = db.execute(_t(q), params).fetchall()
        cols = ["id","account_id","plan_id","strategy_name","signal_date","execution_date",
                "code","stock_name","action","shares","fill_price","fill_time","fill_source",
                "execution_time_model","fee","tax","slippage","gross_amount","net_amount",
                "note","no_lookahead_pass","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/strategy-accounts/{account_id}/positions")
def api_v5_positions(account_id: int):
    """帳戶持倉"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT p.code, sm.name, p.lots, p.avg_cost,
                   o.close, (o.close - p.avg_cost) * p.lots as unrealized_pnl,
                   (o.close/p.avg_cost - 1)*100 as pnl_pct
            FROM positions p
            LEFT JOIN stock_meta sm ON sm.code=p.code
            LEFT JOIN ohlcv_daily o ON o.code=p.code
                AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
            WHERE p.account_id=:id AND p.lots > 0
            ORDER BY unrealized_pnl DESC
        """), {"id": account_id}).fetchall()
        return [{"code": r[0], "name": r[1], "lots": r[2], "avg_cost": r[3],
                 "current_price": r[4], "unrealized_pnl": round(float(r[5] or 0), 0),
                 "pnl_pct": round(float(r[6] or 0), 2)} for r in rows]
    finally:
        db.close()


@app.get("/api/strategy-accounts/{account_id}/equity")
def api_v5_equity(account_id: int, start_date: str = None):
    """帳戶 equity 曲線"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = "SELECT snap_date, cash, market_value, total_equity, daily_return FROM equity_curve WHERE account_id=:id"
        params = {"id": account_id}
        if start_date: q += " AND snap_date>=:sd"; params["sd"] = start_date
        q += " ORDER BY snap_date"
        rows = db.execute(_t(q), params).fetchall()
        base = float(rows[0][3] or 200000) if rows else 200000
        return [{"date": r[0], "cash": r[1], "market_value": r[2],
                 "total_equity": r[3], "daily_return": r[4],
                 "cumulative_return": round((float(r[3] or base)/base-1)*100, 3)} for r in rows]
    finally:
        db.close()


@app.post("/api/strategies/run-v5-pipeline")
def api_run_v5_pipeline(target_date: str = None):
    """執行 V5 完整 daily pipeline"""
    from backend.v5.paper_engine import (check_stop_loss_take_profit,
                                          simulate_paper_fills, update_v5_equity)
    from backend.v5.decision_engine import generate_strategy_decisions
    from backend.v5.benchmark import rebuild_0050_benchmark
    from datetime import date as ddate
    td = ddate.fromisoformat(target_date) if target_date else ddate.today()
    r1 = check_stop_loss_take_profit(td)
    r2 = generate_strategy_decisions(td)
    r3 = simulate_paper_fills(td)
    r4 = update_v5_equity(td)
    rebuild_0050_benchmark()
    return {"ok": True, "sells": r1, "decisions": r2, "fills": r3, "equity": r4}
'''

if "/api/paper/simulate-fills" not in c:
    c = c + V5B_APIS
    print("✓ V5B APIs 加入")
else:
    print("- V5B APIs 已存在")

with open("main.py","w") as f:
    f.write(c)

# ── 2. 把 V5 pipeline 加入每日工作流程 ──
with open("backend/v4/daily_workflow.py") as f:
    wf = f.read()

if "v5_daily_pipeline" not in wf and "paper_engine" not in wf:
    # 在最後一步前加入
    wf = wf.replace(
        '    # Step 11: 輸出日報告',
        '''    # Step 10c: V5 Paper Pipeline
    def _v5_pipeline():
        try:
            from backend.v5.paper_engine import (
                check_stop_loss_take_profit, simulate_paper_fills, update_v5_equity)
            from backend.v5.decision_engine import generate_strategy_decisions
            from backend.v5.benchmark import rebuild_0050_benchmark
            r1 = check_stop_loss_take_profit(target_date)
            r2 = generate_strategy_decisions(target_date)
            r3 = simulate_paper_fills(target_date)
            r4 = update_v5_equity(target_date)
            rebuild_0050_benchmark()
            return {"status": "PASS",
                    "message": f"V5: {r2.get('decisions',0)}筆決策 {r3.get('filled',0)}筆成交"}
        except Exception as e:
            return {"status": "WARN", "message": f"V5 pipeline 失敗: {e}"}
    step("10c_v5_pipeline", _v5_pipeline)

    # Step 11: 輸出日報告'''
    )
    with open("backend/v4/daily_workflow.py","w") as f:
        f.write(wf)
    print("✓ V5 pipeline 加入每日工作流程")
else:
    print("- V5 pipeline 已存在")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ main.py 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())

r2 = subprocess.run(["python3","-m","py_compile","backend/v4/daily_workflow.py"], capture_output=True)
print("✓ daily_workflow 語法正確" if r2.returncode==0 else "❌ "+r2.stderr.decode())
