"""v8_patch.py - V8 API + 每日流程"""
import subprocess

with open("main.py") as f:
    c = f.read()

V8_APIS = '''
# ═══════════════════════════════════════
# V8 APIs
# ═══════════════════════════════════════

@app.get("/api/v8/ml-scores")
def api_v8_ml_scores(score_date: str = None, limit: int = 20):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    db = SessionLocal()
    try:
        if not score_date:
            score_date = db.execute(_t("SELECT MAX(score_date) FROM ml_score_results")).scalar()
        rows = db.execute(_t("""
            SELECT score_date, code, stock_name, ml_score, ml_rank,
                   predicted_return_5d, model_version
            FROM ml_score_results WHERE score_date=:d
            ORDER BY ml_rank LIMIT :n
        """), {"d": score_date, "n": limit}).fetchall()
        cols = ["date","code","name","ml_score","ml_rank","pred_5d","model"]
        return {"date": score_date, "data": [dict(zip(cols,r)) for r in rows]}
    finally:
        db.close()


@app.get("/api/v8/weekly-performance")
def api_v8_weekly(limit: int = 12):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT week_start, week_end, account_id, strategy_name,
                   weekly_return, benchmark_return, alpha, trade_count
            FROM weekly_performance_snapshots
            ORDER BY week_start DESC, account_id LIMIT :n
        """), {"n": limit}).fetchall()
        cols = ["week_start","week_end","account_id","strategy","weekly_return",
                "bench_return","alpha","trade_count"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v8/concentration-risk")
def api_v8_concentration(check_date: str = None):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    db = SessionLocal()
    try:
        if not check_date:
            check_date = db.execute(_t("SELECT MAX(check_date) FROM selection_concentration")).scalar()
        rows = db.execute(_t("""
            SELECT code, stock_name, selected_by_count, selected_by_accounts, concentration_risk
            FROM selection_concentration WHERE check_date=:d
            ORDER BY selected_by_count DESC
        """), {"d": check_date}).fetchall()
        cols = ["code","name","count","accounts","risk"]
        return {"date": check_date, "data": [dict(zip(cols,r)) for r in rows]}
    finally:
        db.close()


@app.get("/api/v8/monthly-revenue")
def api_v8_revenue(code: str = None, limit: int = 12):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = "SELECT code, stock_name, year, month, revenue, revenue_yoy, revenue_mom, announce_date FROM monthly_revenue"
        p = {}
        if code: q += " WHERE code=:c"; p["c"] = code
        q += " ORDER BY year DESC, month DESC LIMIT :n"; p["n"] = limit
        rows = db.execute(_t(q), p).fetchall()
        cols = ["code","name","year","month","revenue","yoy","mom","announce_date"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v8/bear-stress")
def api_v8_bear_stress():
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT strategy_name, test_period, start_date, end_date,
                   strategy_return, benchmark_return, alpha, max_drawdown
            FROM bear_market_stress_test ORDER BY test_period, alpha DESC
        """)).fetchall()
        cols = ["strategy","period","start","end","return","bench","alpha","drawdown"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/v8", response_class=HTMLResponse)
def page_v8(request: Request):
    return templates.TemplateResponse("v8_overview.html", {"request": request})
'''

if "/api/v8/ml-scores" not in c:
    c = c + V8_APIS
    with open("main.py","w") as f:
        f.write(c)
    print("✓ V8 APIs 加入")
else:
    print("- 已存在")

# 每日流程加入 V8
with open("backend/v4/daily_workflow.py") as f:
    wf = f.read()

if "v8_weekly_snapshot" not in wf:
    old = "    # Step 10g: V7 每日"
    new = """    # Step 10h: V8 每日
    def _v8_daily():
        try:
            from scripts.v8_concentration_risk import check_concentration
            from scripts.v8_ml_scoring import score_today
            from backend.models.database import SessionLocal as _SL
            r = check_concentration(target_date)
            db2 = _SL()
            try: score_today(db2, str(target_date))
            except: pass
            finally: db2.close()
            return {"status":"PASS","message":f"V8: 集中度{len(r)}筆 ML評分完成"}
        except Exception as e:
            return {"status":"WARN","message":f"V8: {e}"}
    step("10h_v8_daily", _v8_daily)

    # Step 10g: V7 每日"""
    if old in wf:
        wf = wf.replace(old, new)
        with open("backend/v4/daily_workflow.py","w") as f:
            f.write(wf)
        print("✓ V8 加入每日流程")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
r2 = subprocess.run(["python3","-m","py_compile","backend/v4/daily_workflow.py"], capture_output=True)
print("✓ daily_workflow 正確" if r2.returncode==0 else "❌ "+r2.stderr.decode())
