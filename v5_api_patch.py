"""v5_api_patch.py - 加入 V5 APIs"""
import subprocess

with open("main.py") as f:
    c = f.read()

V5_APIS = '''
# ═══════════════════════════════════════
# V5 APIs
# ═══════════════════════════════════════

@app.get("/api/strategy-accounts")
def api_v5_strategy_accounts():
    """V5 策略帳戶列表"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT a.id, a.name, a.strategy_type, a.initial_cash, a.cash,
                   a.mode,
                   eq.market_value, eq.total_equity, eq.daily_return, eq.snap_date,
                   cfg.strategy_name, cfg.stop_loss_pct, cfg.take_profit_pct,
                   cfg.max_positions, cfg.description
            FROM strategy_accounts a
            LEFT JOIN (
                SELECT account_id, market_value, total_equity, daily_return, snap_date
                FROM equity_curve WHERE snap_date=(SELECT MAX(snap_date) FROM equity_curve)
            ) eq ON eq.account_id=a.id
            LEFT JOIN strategy_account_configs cfg ON cfg.account_id=a.id
            WHERE a.id >= 11
            ORDER BY a.id
        """)).fetchall()
        result = []
        for r in rows:
            init = float(r[3] or 200000)
            total = float(r[7] or r[4] or init)
            result.append({
                "account_id": r[0], "name": r[1], "strategy_type": r[2],
                "initial_cash": init, "cash": float(r[4] or init),
                "mode": r[5] or "forward_paper",
                "market_value": float(r[6] or 0),
                "total_equity": total,
                "total_return": round((total/init-1)*100, 2) if init else 0,
                "daily_return": float(r[8] or 0),
                "last_updated": r[9],
                "strategy_name": r[10],
                "stop_loss_pct": r[11],
                "take_profit_pct": r[12],
                "max_positions": r[13],
                "description": r[14],
            })
        return result
    finally:
        db.close()


@app.get("/api/strategy-accounts/{account_id}/decisions")
def api_v5_decisions(account_id: int, signal_date: str = None, limit: int = 20):
    """V5 策略決策記錄"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = """SELECT id, signal_date, execution_date, code, action,
                      final_score, suggested_shares, reference_price,
                      stop_loss, target_price, is_blocked, blocked_reason, reason_summary
               FROM strategy_decision_logs WHERE account_id=:aid"""
        params = {"aid": account_id}
        if signal_date: q += " AND signal_date=:sd"; params["sd"] = signal_date
        q += " ORDER BY id DESC LIMIT :n"; params["n"] = limit
        rows = db.execute(_t(q), params).fetchall()
        cols = ["id","signal_date","execution_date","code","action",
                "final_score","suggested_shares","reference_price",
                "stop_loss","target_price","is_blocked","blocked_reason","reason_summary"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.post("/api/strategies/generate-v5-decisions")
def api_generate_v5_decisions(signal_date: str = None):
    """產生 V5 策略決策"""
    from backend.v5.decision_engine import generate_strategy_decisions
    from datetime import date as ddate
    sd = ddate.fromisoformat(signal_date) if signal_date else ddate.today()
    return generate_strategy_decisions(sd)


@app.get("/api/benchmark/0050")
def api_benchmark_0050(start_date: str = None, end_date: str = None):
    """0050 Buy and Hold Benchmark"""
    from backend.v5.benchmark import get_benchmark_equity
    return get_benchmark_equity(start_date=start_date, end_date=end_date)


@app.post("/api/benchmark/rebuild")
def api_rebuild_benchmark(start_date: str = "2025-01-01"):
    """重建 0050 Benchmark"""
    from backend.v5.benchmark import rebuild_0050_benchmark
    n = rebuild_0050_benchmark(start_date=start_date)
    return {"ok": True, "records": n}


@app.get("/api/monthly/race")
def api_monthly_race(start_date: str = None):
    """月度競賽排行"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    if not start_date:
        today = ddate.today()
        start_date = f"{today.year}-{today.month:02d}-01"
    db = SessionLocal()
    try:
        # 策略帳戶月報酬
        rows = db.execute(_t("""
            SELECT a.id, a.name, a.initial_cash,
                   MIN(eq.total_equity) as start_eq,
                   MAX(eq.total_equity) as end_eq,
                   COUNT(eq.id) as days,
                   MIN(eq.total_equity) as min_eq,
                   MAX(eq.snap_date) as latest_date
            FROM strategy_accounts a
            LEFT JOIN equity_curve eq ON eq.account_id=a.id
                AND eq.snap_date >= :sd
            WHERE a.id >= 11
            GROUP BY a.id, a.name, a.initial_cash
            ORDER BY end_eq DESC
        """), {"sd": start_date}).fetchall()

        # 0050 benchmark 月報酬
        bench = db.execute(_t("""
            SELECT MIN(equity) as start_eq, MAX(equity) as end_eq
            FROM benchmark_daily_equity
            WHERE snap_date >= :sd AND benchmark_code='0050'
        """), {"sd": start_date}).fetchone()
        bench_start = float(bench[0] or 200000) if bench else 200000
        bench_end   = float(bench[1] or 200000) if bench else 200000
        bench_ret   = round((bench_end/bench_start-1)*100, 2) if bench_start else 0

        results = []
        for i, r in enumerate(rows):
            init = float(r[2] or 200000)
            start_eq = float(r[3] or init)
            end_eq   = float(r[4] or init)
            monthly_ret = round((end_eq/start_eq-1)*100, 2) if start_eq else 0
            alpha = round(monthly_ret - bench_ret, 2)

            results.append({
                "rank": i+1,
                "account_id": r[0],
                "account_name": r[1],
                "monthly_return": monthly_ret,
                "benchmark_0050_return": bench_ret,
                "alpha_vs_0050": alpha,
                "outperform": alpha > 0,
                "total_equity": end_eq,
                "trading_days": r[5],
                "latest_date": r[7],
            })

        return {
            "start_date": start_date,
            "benchmark_return": bench_ret,
            "accounts": results,
        }
    finally:
        db.close()


@app.get("/api/monthly/equity-curves")
def api_monthly_equity_curves(start_date: str = None):
    """月度競賽淨值曲線（含0050）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    if not start_date:
        today = ddate.today()
        start_date = f"{today.year}-{today.month:02d}-01"
    db = SessionLocal()
    try:
        # 策略帳戶淨值
        accounts = db.execute(_t("""
            SELECT DISTINCT a.id, a.name FROM strategy_accounts a
            WHERE a.id >= 11
        """)).fetchall()

        curves = []
        for aid, aname in accounts:
            rows = db.execute(_t("""
                SELECT snap_date, total_equity FROM equity_curve
                WHERE account_id=:id AND snap_date>=:sd
                ORDER BY snap_date
            """), {"id": aid, "sd": start_date}).fetchall()
            if rows:
                base = float(rows[0][1] or 200000)
                curves.append({
                    "account_id": aid,
                    "name": aname,
                    "curve": [{"date": r[0],
                                "total": float(r[1] or base),
                                "return_pct": round((float(r[1] or base)/base-1)*100, 3)}
                               for r in rows],
                })

        # 0050 benchmark
        bench_rows = db.execute(_t("""
            SELECT snap_date, equity FROM benchmark_daily_equity
            WHERE benchmark_code='0050' AND snap_date>=:sd
            ORDER BY snap_date
        """), {"sd": start_date}).fetchall()

        if bench_rows:
            base_b = float(bench_rows[0][1] or 200000)
            curves.append({
                "account_id": 0,
                "name": "0050 Buy&Hold",
                "is_benchmark": True,
                "curve": [{"date": r[0],
                            "total": float(r[1] or base_b),
                            "return_pct": round((float(r[1] or base_b)/base_b-1)*100, 3)}
                           for r in bench_rows],
            })

        return curves
    finally:
        db.close()
'''

if "/api/strategy-accounts" not in c and "api_v5_strategy_accounts" not in c:
    c = c + V5_APIS
    print("✓ V5 APIs 加入")
else:
    print("- V5 APIs 已存在")

with open("main.py","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
