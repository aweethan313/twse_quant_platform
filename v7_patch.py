"""v7_patch.py - V7 API + 每日流程整合"""
import subprocess

with open("main.py") as f:
    c = f.read()

V7_APIS = '''
# ═══════════════════════════════════════
# V7 APIs
# ═══════════════════════════════════════

@app.get("/api/v7/market-timing")
def api_v7_market_timing(days: int = 30):
    """大盤擇時訊號歷史"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT trade_date, close, ma20, ma60, above_ma20, above_ma60,
                   risk_level, position_multiplier, reason_summary
            FROM market_timing_signals
            ORDER BY trade_date DESC LIMIT :n
        """), {"n": days}).fetchall()
        cols = ["date","close","ma20","ma60","above_ma20","above_ma60",
                "risk_level","position_multiplier","reason"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v7/market-timing/today")
def api_v7_market_timing_today():
    """今日擇時訊號"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        r = db.execute(_t("""
            SELECT trade_date, risk_level, position_multiplier, reason_summary
            FROM market_timing_signals ORDER BY trade_date DESC LIMIT 1
        """)).fetchone()
        if not r: return {"risk_level":"medium","position_multiplier":1.0,"reason":"無擇時資料"}
        return {"date":r[0],"risk_level":r[1],"position_multiplier":float(r[2] or 1),"reason":r[3]}
    finally:
        db.close()


@app.get("/api/v7/sector-rotation")
def api_v7_sector_rotation(trade_date: str = None):
    """產業輪動排名"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        if not trade_date:
            trade_date = db.execute(_t(
                "SELECT MAX(trade_date) FROM sector_theme_rotation"
            )).scalar()
        rows = db.execute(_t("""
            SELECT theme_name, stock_count, avg_return_1d, avg_return_5d,
                   chip_strength, theme_strength_score, rank
            FROM sector_theme_rotation WHERE trade_date=:d ORDER BY rank
        """), {"d": trade_date}).fetchall()
        cols = ["theme","n","ret_1d","ret_5d","chip","score","rank"]
        return {"date": trade_date, "data": [dict(zip(cols,r)) for r in rows]}
    finally:
        db.close()


@app.get("/api/v7/events")
def api_v7_events(days: int = 30):
    """財報/月營收事件"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate, timedelta as td
    db = SessionLocal()
    try:
        from_date = str(ddate.today() - td(days=days))
        rows = db.execute(_t("""
            SELECT e.code, e.stock_name, e.event_type, e.event_date,
                   er.return_5d, er.conclusion
            FROM stock_event_calendar e
            LEFT JOIN event_return_analysis er ON er.event_id=e.id
            WHERE e.event_date >= :d
            ORDER BY e.event_date DESC LIMIT 50
        """), {"d": from_date}).fetchall()
        cols = ["code","name","event_type","event_date","return_5d","conclusion"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v7/factor-analysis")
def api_v7_factor_analysis():
    """多因子分析結果"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT factor_name, ic_mean, hit_rate, avg_return_5d, suggested_weight, note
            FROM factor_analysis_results
            ORDER BY created_at DESC
        """)).fetchall()
        cols = ["factor","ic","hit_rate","avg_5d","suggested_weight","note"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v7/us-events")
def api_v7_us_events(days: int = 60):
    """美股重大事件"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate, timedelta as td
    db = SessionLocal()
    try:
        from_date = str(ddate.today() - td(days=days))
        rows = db.execute(_t("""
            SELECT event_date, ticker, event_type, change_pct, tw_semi_impact_5d, note
            FROM us_market_events WHERE event_date >= :d
            ORDER BY event_date DESC LIMIT 30
        """), {"d": from_date}).fetchall()
        cols = ["date","ticker","event_type","change_pct","tw_impact_5d","note"]
        return [dict(zip(cols,r)) for r in rows]
    finally:
        db.close()


@app.get("/v7", response_class=HTMLResponse)
def page_v7(request: Request):
    return templates.TemplateResponse("v7_overview.html", {"request": request})

@app.get("/v7/market-timing", response_class=HTMLResponse)
def page_v7_timing(request: Request):
    return templates.TemplateResponse("v7_market_timing.html", {"request": request})

@app.get("/v7/sector-rotation", response_class=HTMLResponse)
def page_v7_rotation(request: Request):
    return templates.TemplateResponse("v7_sector_rotation.html", {"request": request})
'''

if "/api/v7/market-timing" not in c:
    c = c + V7_APIS
    with open("main.py","w") as f:
        f.write(c)
    print("✓ V7 APIs 加入")
else:
    print("- V7 APIs 已存在")

# 每日流程加入 V7
with open("backend/v4/daily_workflow.py") as f:
    wf = f.read()

if "v7_market_timing" not in wf:
    old = "    # Step 10d: V6 每日"
    new = """    # Step 10g: V7 每日
    def _v7_daily():
        try:
            from scripts.v7_market_timing import update_market_timing
            from scripts.v7_sector_rotation import update_sector_rotation
            update_market_timing(target_date)
            update_sector_rotation(target_date)
            return {"status":"PASS","message":"V7: 擇時+輪動更新"}
        except Exception as e:
            return {"status":"WARN","message":f"V7: {e}"}
    step("10g_v7_daily", _v7_daily)

    # Step 10d: V6 每日"""
    if old in wf:
        wf = wf.replace(old, new)
        with open("backend/v4/daily_workflow.py","w") as f:
            f.write(wf)
        print("✓ V7 加入每日流程")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
r2 = subprocess.run(["python3","-m","py_compile","backend/v4/daily_workflow.py"], capture_output=True)
print("✓ daily_workflow 正確" if r2.returncode==0 else "❌ "+r2.stderr.decode())
