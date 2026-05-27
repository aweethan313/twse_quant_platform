"""v6d_patch.py - V6D 最終整合"""
import subprocess

with open("main.py") as f:
    c = f.read()

V6D_APIS = '''
# ─── V6D 補充 APIs ───

@app.get("/v6/selection-heatmap", response_class=HTMLResponse)
def page_v6_heatmap(request: Request):
    return templates.TemplateResponse("v6_selection_heatmap.html", {"request": request})

@app.get("/stock/{code}", response_class=HTMLResponse)
def page_stock_detail(request: Request, code: str):
    return templates.TemplateResponse("stock_detail.html", {"request": request, "code": code})

@app.get("/stock", response_class=HTMLResponse)
def page_stock_search(request: Request):
    return templates.TemplateResponse("stock_detail.html", {"request": request, "code": ""})

@app.get("/api/v6/selection-heatmap")
def api_v6_selection_heatmap(days: int = 30):
    """選股回顧熱圖"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate, timedelta as td
    db = SessionLocal()
    try:
        start = str(ddate.today() - td(days=days))

        # 取決策記錄
        rows = db.execute(_t("""
            SELECT sdl.signal_date, sdl.code, sm.name,
                   sdl.final_score, sdl.account_id
            FROM strategy_decision_logs sdl
            LEFT JOIN stock_meta sm ON sm.code=sdl.code
            WHERE sdl.signal_date >= :s AND sdl.action='BUY' AND sdl.is_blocked=0
            ORDER BY sdl.signal_date, sdl.code
        """), {"s": start}).fetchall()

        # 取前瞻報酬
        fwd_rows = db.execute(_t("""
            SELECT signal_date, code, return_5d, alpha_5d_vs_0050
            FROM candidate_forward_returns
            WHERE signal_date >= :s
        """), {"s": start}).fetchall()
        fwd_map = {(r[0],r[1]): {"ret": r[2], "alpha": r[3]} for r in fwd_rows}

        # 建立矩陣
        dates = sorted(set(r[0] for r in rows))
        stocks_map = {}
        for sig_date, code, name, score, aid in rows:
            if code not in stocks_map:
                stocks_map[code] = {"code": code, "name": name}

        matrix = {}
        for sig_date, code, name, score, aid in rows:
            if code not in matrix: matrix[code] = {}
            fwd = fwd_map.get((str(sig_date), code), {})
            ret = fwd.get("ret")
            result = "PENDING"
            if ret is not None:
                result = "WIN" if ret > 0 else "LOSS" if ret < 0 else "FLAT"
            matrix[code][str(sig_date)] = {
                "ret": round(float(ret), 2) if ret is not None else None,
                "result": result,
                "score": float(score or 0),
            }

        # 股票統計
        stock_stats = []
        for code, data in matrix.items():
            rets = [v["ret"] for v in data.values() if v["ret"] is not None]
            wins = sum(1 for r in rets if r > 0)
            alphas = [fwd_map.get((d, code), {}).get("alpha") for d in data]
            alphas = [a for a in alphas if a is not None]
            stock_stats.append({
                "code": code,
                "name": stocks_map.get(code, {}).get("name", code),
                "count": len(data),
                "win_rate": wins/len(rets)*100 if rets else 0,
                "avg_5d": sum(rets)/len(rets) if rets else 0,
                "avg_alpha": sum(alphas)/len(alphas) if alphas else 0,
            })
        stock_stats.sort(key=lambda x: x["win_rate"], reverse=True)

        return {
            "dates": list(dates),
            "stocks": [{"code": k, "name": v["name"]} for k,v in stocks_map.items()],
            "matrix": matrix,
            "stock_stats": stock_stats[:20],
        }
    finally:
        db.close()


@app.get("/api/v6/stock-positions")
def api_v6_stock_positions(code: str):
    """某股票在各帳戶的持倉"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT p.account_id, a.name, p.lots, p.avg_cost, o.close,
                   (o.close/p.avg_cost-1)*100 as pnl_pct
            FROM positions p
            JOIN strategy_accounts a ON a.id=p.account_id
            LEFT JOIN ohlcv_daily o ON o.code=p.code
                AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
            WHERE p.code=:c AND p.lots > 0
        """), {"c": code}).fetchall()
        return [{"account_id": r[0], "account_name": r[1], "lots": r[2],
                 "avg_cost": r[3], "current_price": r[4],
                 "pnl_pct": round(float(r[5] or 0), 2)} for r in rows]
    finally:
        db.close()


@app.get("/api/v6/daily-report")
def api_v6_daily_report(report_date: str = None):
    """取得 V6 每日報告"""
    from backend.v6.daily_report_v6 import generate_daily_report_v6
    from datetime import date as ddate
    d = ddate.fromisoformat(report_date) if report_date else ddate.today()
    report = generate_daily_report_v6(d)
    return {"date": str(d), "report": report}
'''

if "/stock/{code}" not in c:
    c = c + V6D_APIS
    print("✓ V6D APIs 加入")
else:
    print("- V6D APIs 已存在")

# 加入 V6D 到每日報告
with open("backend/v4/daily_workflow.py") as f:
    wf = f.read()

if "v6_daily_report" not in wf and "daily_report_v6" not in wf:
    old = "    # Step 11: 輸出日報告"
    new = """    # Step 10f: V6 每日報告
    def _v6_report():
        try:
            from backend.v6.daily_report_v6 import generate_daily_report_v6
            generate_daily_report_v6(target_date)
            return {"status":"PASS","message":f"V6每日報告輸出"}
        except Exception as e:
            return {"status":"WARN","message":f"V6報告失敗: {e}"}
    step("10f_v6_report", _v6_report)

    # Step 11: 輸出日報告"""
    if old in wf:
        wf = wf.replace(old, new)
        with open("backend/v4/daily_workflow.py","w") as f:
            f.write(wf)
        print("✓ V6報告加入每日工作流程")

with open("main.py","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ main.py 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
r2 = subprocess.run(["python3","-m","py_compile","backend/v4/daily_workflow.py"], capture_output=True)
print("✓ daily_workflow 語法正確" if r2.returncode==0 else "❌ "+r2.stderr.decode())
