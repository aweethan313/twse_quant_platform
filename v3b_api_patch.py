"""v3b_api_patch.py - 套用到 main.py"""
with open("main.py") as f:
    c = f.read()

V3B_APIS = '''
# ═══════════════════════════════════
# V3b APIs (FIX-10~15)
# ═══════════════════════════════════

@app.get("/api/capital/config")
def api_capital_config():
    """V3-FIX-15 資金與風險設定"""
    try:
        from config.capital_config import CAPITAL_CONFIG
        return CAPITAL_CONFIG.summary()
    except Exception as e:
        return {"mode":"assistive","allow_auto_order":False,
                "require_user_confirmation":True,"error":str(e)}


@app.get("/api/candidates/trade-plans")
def api_candidate_trade_plans(
    query_date: str = None, code: str = None,
    candidate_pool_type: str = None, limit: int = 50
):
    """V3-FIX-11 候選股交易計畫"""
    from backend.v3.candidate_trade_plans import get_trade_plans, generate_daily_plans
    from datetime import date as ddate
    plans = get_trade_plans(plan_date=query_date, code=code, limit=limit)
    if not plans and not query_date:
        plans = generate_daily_plans(ddate.today(), limit=limit)
    return plans


@app.get("/api/watchlist/alerts")
def api_watchlist_alerts(
    alert_date: str = None, code: str = None, limit: int = 100
):
    """V3-FIX-13 看盤提醒"""
    from backend.v3.watchlist_alerts import get_alerts
    return get_alerts(alert_date=alert_date, code=code, limit=limit)


@app.get("/api/candidates/accuracy")
def api_candidates_accuracy(
    strategy_id: int = None, candidate_pool_type: str = None,
    code: str = None, start_date: str = None, end_date: str = None, limit: int = 100
):
    """V3-FIX-14 候選股勝率追蹤"""
    from backend.v3.watchlist_alerts import get_accuracy_list, get_accuracy_stats
    return {
        "stats": get_accuracy_stats(strategy_id=strategy_id,
                                    candidate_pool_type=candidate_pool_type,
                                    start_date=start_date, end_date=end_date),
        "records": get_accuracy_list(code=code, limit=limit),
    }


@app.get("/api/candidates/news")
def api_candidates_news(code: str = None, query_date: str = None, limit: int = 10):
    """V3-FIX-12 候選股新聞（框架）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        q = "SELECT * FROM candidate_news WHERE 1=1"
        params = {}
        if code: q += " AND code=:code"; params["code"] = code
        if query_date: q += " AND news_time<=:d"; params["d"] = query_date+" 23:59:59"
        q += " ORDER BY news_time DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(_text(q), params).fetchall()
        cols = ["id","news_time","code","name","title","source",
                "source_credibility_score","sentiment","related_themes",
                "is_official_disclosure","is_financial_report",
                "is_monthly_revenue","is_investor_conference","summary","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
'''

TARGET = "# ─── 啟動 ───" if "# ─── 啟動 ───" in c else 'if __name__ == "__main__"'
if "/api/capital/config" not in c:
    if TARGET in c:
        c = c.replace(TARGET, V3B_APIS + "\n" + TARGET)
    else:
        c += "\n" + V3B_APIS
    print("✓ V3b APIs 加入")
else:
    print("- V3b APIs 已存在")

with open("main.py","w") as f:
    f.write(c)

import subprocess
r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ 語法錯誤: "+r.stderr.decode())
