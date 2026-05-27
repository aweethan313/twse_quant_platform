"""v6_api_patch.py - 加入 V6 APIs"""
import subprocess

with open("main.py") as f:
    c = f.read()

V6_APIS = '''
# ═══════════════════════════════════════
# V6 APIs
# ═══════════════════════════════════════

@app.get("/api/v6/backtest/results")
def api_v6_backtest_results():
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT strategy_name, start_date, end_date,
                   total_return, benchmark_0050_return, alpha_vs_0050,
                   annualized_return, max_drawdown, win_rate,
                   trade_count, profit_factor, average_win, average_loss,
                   fee_total, created_at
            FROM v6_strategy_backtest_results
            ORDER BY created_at DESC, alpha_vs_0050 DESC
        """)).fetchall()
        cols = ["strategy_name","start_date","end_date","total_return","benchmark_0050_return",
                "alpha_vs_0050","annualized_return","max_drawdown","win_rate",
                "trade_count","profit_factor","average_win","average_loss","fee_total","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v6/strategy-health")
def api_v6_strategy_health():
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT strategy_name, account_id, eval_start_date, eval_end_date,
                   alpha_vs_0050, max_drawdown, win_rate, profit_factor,
                   trade_count, health_score, recommendation, reason_summary, created_at
            FROM strategy_health_scores
            ORDER BY created_at DESC
        """)).fetchall()
        cols = ["strategy_name","account_id","eval_start_date","eval_end_date",
                "alpha_vs_0050","max_drawdown","win_rate","profit_factor",
                "trade_count","health_score","recommendation","reason_summary","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v6/cooldowns")
def api_v6_cooldowns(active_only: bool = True):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = """SELECT sc.*, sm.name as stock_name_meta FROM strategy_cooldowns sc
               LEFT JOIN stock_meta sm ON sm.code=sc.code"""
        if active_only: q += " WHERE sc.is_active=1"
        q += " ORDER BY sc.cooldown_until DESC"
        rows = db.execute(_t(q)).fetchall()
        cols = ["id","account_id","strategy_name","code","stock_name","triggered_date",
                "stop_loss_price","exit_price","cooldown_days","cooldown_until",
                "reason","is_active","lifted_date","lifted_reason","created_at","updated_at","meta_name"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v6/chip-alerts")
def api_v6_chip_alerts(trade_date: str = None, severity: str = None, limit: int = 50):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    db = SessionLocal()
    try:
        if not trade_date:
            trade_date = db.execute(_t("SELECT MAX(trade_date) FROM chip_anomaly_alerts")).scalar()
        q = "SELECT * FROM chip_anomaly_alerts WHERE trade_date=:d"
        params = {"d": trade_date}
        if severity: q += " AND severity=:sev"; params["sev"] = severity
        q += " ORDER BY CASE severity WHEN 'RISK' THEN 1 WHEN 'STRONG' THEN 2 WHEN 'WATCH' THEN 3 ELSE 4 END LIMIT :n"
        params["n"] = limit
        rows = db.execute(_t(q), params).fetchall()
        cols = ["id","trade_date","code","stock_name","alert_type","investor_type",
                "buy_sell_value","buy_sell_volume","streak_days","volume_ratio",
                "severity","score_impact_suggestion","reason_summary","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/v6/candidate-score-buckets")
def api_v6_candidate_score_buckets():
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT score_bucket,
                   COUNT(*) as n,
                   ROUND(AVG(return_5d),3) as avg_5d,
                   ROUND(AVG(return_10d),3) as avg_10d,
                   ROUND(AVG(return_20d),3) as avg_20d,
                   ROUND(AVG(alpha_5d_vs_0050),3) as avg_alpha5,
                   ROUND(SUM(CASE WHEN return_5d>0 THEN 1.0 ELSE 0 END)*100/COUNT(*),1) as win_rate_5d,
                   ROUND(AVG(max_drawdown_20d),3) as avg_dd
            FROM candidate_forward_returns
            WHERE return_5d IS NOT NULL
            GROUP BY score_bucket ORDER BY score_bucket DESC
        """)).fetchall()
        cols = ["score_bucket","n","avg_5d","avg_10d","avg_20d","avg_alpha5","win_rate_5d","avg_dd"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.post("/api/v6/backtest/run")
def api_v6_run_backtest(start_date: str = "2025-01-01", end_date: str = None):
    from scripts.v6_backtest_validate_strategies import run
    return {"ok": True, "results": run(start_date, end_date or "latest")}


@app.post("/api/v6/strategy-health/rebuild")
def api_v6_rebuild_health():
    from scripts.v6_update_strategy_health_scores import update_health_scores
    update_health_scores()
    return {"ok": True}


@app.post("/api/v6/chip-alerts/detect")
def api_v6_detect_chips(trade_date: str = None):
    from scripts.v6_detect_chip_anomalies import detect_chip_anomalies
    from datetime import date as ddate
    d = ddate.fromisoformat(trade_date) if trade_date else ddate.today()
    n = detect_chip_anomalies(d)
    return {"ok": True, "alerts": n}


@app.get("/api/v6/candidate-forward-returns")
def api_v6_candidate_forward_returns(signal_date: str = None, limit: int = 50):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        if not signal_date:
            signal_date = db.execute(_t("SELECT MAX(signal_date) FROM candidate_forward_returns")).scalar()
        rows = db.execute(_t("""
            SELECT signal_date, code, stock_name, candidate_score, score_bucket,
                   rank, close_price, return_1d, return_5d, return_10d, return_20d,
                   alpha_5d_vs_0050, alpha_10d_vs_0050
            FROM candidate_forward_returns WHERE signal_date=:sd
            ORDER BY candidate_score DESC LIMIT :n
        """), {"sd": signal_date, "n": limit}).fetchall()
        cols = ["signal_date","code","stock_name","candidate_score","score_bucket",
                "rank","close_price","return_1d","return_5d","return_10d","return_20d",
                "alpha_5d_vs_0050","alpha_10d_vs_0050"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/strategy-decisions")
def api_strategy_decisions(signal_date: str = None, account_id: int = None, limit: int = 30):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_decision_logs WHERE 1=1"
        p = {}
        if signal_date: q += " AND signal_date=:sd"; p["sd"] = signal_date
        if account_id:  q += " AND account_id=:aid"; p["aid"] = account_id
        q += " ORDER BY id DESC LIMIT :n"; p["n"] = limit
        rows = db.execute(_t(q), p).fetchall()
        cols = ["id","account_id","strategy_name","mode","signal_date",
                "data_cutoff_time","execution_date","execution_time_model",
                "code","action","candidate_score","final_score","risk_score",
                "suggested_shares","reference_price","expected_fill_price",
                "stop_loss","target_price","is_blocked","blocked_reason","reason_summary",
                "no_lookahead_pass","created_at"]
        return [dict(zip(cols[:len(r)], r)) for r in rows]
    finally:
        db.close()
'''

if "/api/v6/backtest/results" not in c:
    c = c + V6_APIS
    print("✓ V6 APIs 加入")
else:
    print("- V6 APIs 已存在")

with open("main.py","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
