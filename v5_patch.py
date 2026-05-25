"""v5_patch.py - 套用 V5 patches"""
import subprocess, re

# ── 1. latest_update 加入 technical_features 步驟 ──
with open("backend/services/latest_update.py") as f:
    c = f.read()

if "technical_features" not in c:
    c = c.replace(
        'steps.append(_step("equity_snapshot", lambda: _snapshot_equity(target_date)))',
        '''steps.append(_step("technical_features", lambda: _build_tech(target_date)))
        steps.append(_step("equity_snapshot", lambda: _snapshot_equity(target_date)))'''
    )
    TECH_FN = '''
def _build_tech(target_date) -> dict:
    """計算技術指標"""
    from backend.services.technical_features import build_technical_features
    n = build_technical_features(target_date)
    return {"ok": True, "updated": n}

'''
    c = c.replace("def _snapshot_equity", TECH_FN + "def _snapshot_equity")
    with open("backend/services/latest_update.py","w") as f:
        f.write(c)
    print("✓ technical_features 加入每日更新流程")
else:
    print("- technical_features 已存在")

# ── 2. main.py 加入 V5 APIs ──
with open("main.py") as f:
    c = f.read()

V5_APIS = '''
# ═══════════════════════════════════
# V5 APIs
# ═══════════════════════════════════

@app.get("/api/data-quality/technical")
def api_technical_coverage(trade_date: str = None):
    """技術指標覆蓋率"""
    from backend.services.technical_features import get_coverage_stats
    return get_coverage_stats(trade_date)


@app.get("/api/technical/{code}")
def api_technical_features(code: str, trade_date: str = None):
    """單股技術指標"""
    from backend.services.technical_features import get_technical_features
    result = get_technical_features(code, trade_date)
    return result or {"error": f"{code} 無技術指標資料"}


@app.get("/api/freshness")
def api_data_freshness():
    """資料新鮮度總覽"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        tables = {
            "ohlcv_daily":            "SELECT MAX(trade_date) FROM ohlcv_daily",
            "daily_scores":           "SELECT MAX(score_date) FROM daily_scores",
            "technical_features":     "SELECT MAX(trade_date) FROM technical_daily_features",
            "equity_curve":           "SELECT MAX(snap_date) FROM equity_curve",
            "chip_daily":             "SELECT MAX(trade_date) FROM chip_daily",
            "theme_trend":            "SELECT MAX(context_date) FROM theme_trend_daily",
            "market_context":         "SELECT MAX(context_date) FROM market_context_daily",
            "ohlcv_1min":             "SELECT MAX(date(ts)) FROM ohlcv_1min",
        }
        result = {}
        for name, q in tables.items():
            try:
                v = db.execute(_t(q)).scalar()
                result[name] = str(v) if v else "無資料"
            except:
                result[name] = "表不存在"
        return result
    finally:
        db.close()


@app.get("/api/strategy-decisions")
def api_strategy_decisions(
    account_id: int = None, signal_date: str = None,
    action: str = None, limit: int = 50
):
    """策略決策記錄"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_decision_logs WHERE 1=1"
        params = {}
        if account_id: q += " AND account_id=:aid"; params["aid"] = account_id
        if signal_date: q += " AND signal_date=:sd"; params["sd"] = signal_date
        if action: q += " AND action=:action"; params["action"] = action
        q += " ORDER BY id DESC LIMIT :limit"; params["limit"] = limit
        rows = db.execute(_t(q), params).fetchall()
        cols = ["id","account_id","strategy_name","mode","signal_date",
                "data_cutoff_time","execution_date","execution_time_model",
                "code","action","candidate_score","technical_score","chip_score",
                "fundamental_score","risk_score","final_score","suggested_shares",
                "reference_price","expected_fill_price","stop_loss","target_price",
                "is_blocked","blocked_reason","reason_summary","no_lookahead_pass","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
'''

TARGET = "# ─── 啟動 ───" if "# ─── 啟動 ───" in c else 'if __name__ == "__main__"'
if "/api/data-quality/technical" not in c:
    c = c.replace(TARGET, V5_APIS + "\n" + TARGET) if TARGET in c else c + "\n" + V5_APIS
    print("✓ V5 APIs 加入")
else:
    print("- V5 APIs 已存在")

with open("main.py","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
r2 = subprocess.run(["python3","-m","py_compile","backend/services/latest_update.py"], capture_output=True)
print("✓ latest_update 語法正確" if r2.returncode==0 else "❌ "+r2.stderr.decode())
