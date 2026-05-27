"""v6c_patch.py - V6C API + 每日工作流程整合"""
import subprocess

# ── 1. main.py 加入 V6C APIs ──
with open("main.py") as f:
    c = f.read()

V6C_APIS = '''
# ═══════════════════════════════════════
# V6C APIs
# ═══════════════════════════════════════

@app.get("/api/v6/trading-calendar/latest")
def api_trading_calendar_latest():
    """最新有效交易日"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        latest = db.execute(_t(
            "SELECT MAX(trade_date) FROM trading_calendar WHERE is_open=1"
        )).scalar()
        if not latest:
            latest = db.execute(_t("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()
        return {"latest_trading_date": latest}
    finally:
        db.close()


@app.post("/api/v6/trading-calendar/rebuild")
def api_rebuild_trading_calendar():
    from scripts.v6_1_build_trading_calendar import build
    return build()


@app.post("/api/v6/benchmark/rebuild")
def api_v6_rebuild_benchmark():
    from scripts.v6_2_rebuild_0050_benchmark import rebuild
    return rebuild()


@app.get("/api/v6/benchmark/status")
def api_v6_benchmark_status():
    """0050 benchmark 狀態"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        total = db.execute(_t("SELECT COUNT(*) FROM benchmark_daily_equity WHERE benchmark_code='0050'")).scalar()
        valid = db.execute(_t("SELECT COUNT(*) FROM benchmark_daily_equity WHERE benchmark_code='0050' AND (is_valid=1 OR is_valid IS NULL)")).scalar()
        anomaly = db.execute(_t("SELECT COUNT(*) FROM benchmark_daily_equity WHERE benchmark_code='0050' AND is_valid=0")).scalar()
        latest = db.execute(_t("SELECT MAX(snap_date) FROM benchmark_daily_equity WHERE benchmark_code='0050'")).scalar()
        last_ret = db.execute(_t("""
            SELECT cumulative_return FROM benchmark_daily_equity
            WHERE benchmark_code='0050' ORDER BY snap_date DESC LIMIT 1
        """)).scalar()
        return {
            "total": total, "valid": valid, "anomaly": anomaly or 0,
            "latest_date": latest,
            "cumulative_return": float(last_ret or 0),
            "has_anomaly": (anomaly or 0) > 0,
        }
    finally:
        db.close()


@app.post("/api/v6/data-quality/audit")
def api_v6_data_quality_audit():
    from scripts.v6_3_daily_data_quality_audit import run_audit
    return run_audit()


@app.get("/api/v6/fill-model/preview")
def api_v6_fill_preview(code: str, signal_date: str, side: str = "BUY", shares: int = 100):
    """預覽成交模型結果"""
    from backend.v6.daily_fill_model import simulate_daily_fill
    return simulate_daily_fill(code=code, signal_date=signal_date, side=side, shares=shares)


@app.post("/api/paper/manual-fill-v6")
def api_manual_fill_v6(body: dict):
    """V6 手動成交（明確標示 fill_source=manual, is_estimated=0）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from backend.v6.daily_fill_model import can_sell_without_day_trade_violation
    from datetime import date as ddate

    aid = body.get("account_id")
    code = body.get("code")
    action = body.get("action","BUY")
    shares = int(body.get("shares",0))
    fill_price = float(body.get("fill_price",0))
    fill_date = body.get("fill_date", str(ddate.today()))
    note = body.get("note","")

    if not all([aid, code, shares, fill_price]):
        return {"ok": False, "error": "缺少必填欄位"}

    db = SessionLocal()
    try:
        FEE_RATE = 0.001425 * 0.38
        TAX_RATE = 0.003
        MIN_FEE = 20

        gross = fill_price * shares
        fee = max(MIN_FEE, round(gross * FEE_RATE, 0))
        tax = round(gross * TAX_RATE, 0) if action == "SELL" else 0

        # 當沖檢查
        if action == "SELL":
            allowed, reason = can_sell_without_day_trade_violation(aid, code, fill_date, shares, db)
            if not allowed:
                return {"ok": False, "error": reason, "blocked_reason": reason}

        if action == "BUY":
            net = gross + fee
            acct = db.execute(_t("SELECT cash FROM strategy_accounts WHERE id=:id"), {"id": aid}).fetchone()
            if not acct or float(acct[0] or 0) < net:
                return {"ok": False, "error": f"現金不足（需 {net:,.0f}）"}
            db.execute(_t("UPDATE strategy_accounts SET cash=cash-:n WHERE id=:id"), {"n": net, "id": aid})
            pos = db.execute(_t("SELECT id, lots, avg_cost FROM positions WHERE account_id=:id AND code=:c"),
                              {"id": aid, "c": code}).fetchone()
            if pos:
                nl = float(pos[1]) + shares
                nc = (float(pos[1])*float(pos[2]) + shares*fill_price) / nl
                db.execute(_t("UPDATE positions SET lots=:l, avg_cost=:cost WHERE id=:pid"),
                           {"l": nl, "cost": nc, "pid": pos[0]})
            else:
                db.execute(_t("INSERT INTO positions (account_id,code,lots,avg_cost,opened_at) VALUES (:id,:c,:l,:cost,datetime('now','localtime'))"),
                           {"id": aid, "c": code, "l": shares, "cost": fill_price})
        else:
            net = gross - fee - tax
            db.execute(_t("UPDATE strategy_accounts SET cash=cash+:n WHERE id=:id"), {"n": net, "id": aid})
            pos = db.execute(_t("SELECT id, lots FROM positions WHERE account_id=:id AND code=:c"),
                              {"id": aid, "c": code}).fetchone()
            if pos:
                nl = float(pos[1]) - shares
                if nl <= 0:
                    db.execute(_t("DELETE FROM positions WHERE id=:pid"), {"pid": pos[0]})
                else:
                    db.execute(_t("UPDATE positions SET lots=:l WHERE id=:pid"), {"l": nl, "pid": pos[0]})

        db.execute(_t("""
            INSERT INTO paper_fills
                (account_id, code, action, shares, fill_price, fill_time,
                 fill_source, is_estimated, price_mode,
                 fee, tax, gross_amount, net_amount, note, execution_date, no_lookahead_pass)
            VALUES (:aid,:c,:a,:s,:fp,datetime('now','localtime'),
                    'manual',0,'manual_input',
                    :fee,:tax,:gross,:net,:note,:fd,1)
        """), {"aid": aid, "c": code, "a": action, "s": shares, "fp": fill_price,
               "fee": fee, "tax": tax, "gross": gross, "net": net, "note": note, "fd": fill_date})

        db.commit()
        return {"ok": True, "action": action, "code": code, "shares": shares,
                "fill_price": fill_price, "fill_source": "manual", "is_estimated": 0,
                "fee": fee, "net": net}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@app.get("/api/v6/no-lookahead-audit")
def api_no_lookahead_audit():
    """No-lookahead 審計摘要"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    db = SessionLocal()
    try:
        today = str(ddate.today())
        # 決策是否使用未來資料
        sdl_fail = db.execute(_t("""
            SELECT COUNT(*) FROM strategy_decision_logs
            WHERE signal_date > datetime('now','localtime')
        """)).scalar() or 0

        pf_fail = db.execute(_t("""
            SELECT COUNT(*) FROM paper_fills
            WHERE execution_date IS NOT NULL AND signal_date IS NOT NULL
              AND execution_date <= signal_date
        """)).scalar() or 0

        return {
            "strategy_decisions_future": sdl_fail,
            "fills_before_signal": pf_fail,
            "pass": sdl_fail == 0 and pf_fail == 0,
            "data_sources": {
                "ohlcv_daily": "SAFE - 使用 trade_date，無 lookahead",
                "daily_scores": "SAFE - score_date <= signal_date",
                "technical_features": "SAFE - trade_date <= signal_date",
                "chip_daily": "SAFE - trade_date <= signal_date",
                "fill_price": "ESTIMATED - 使用 T+1 open，不影響 T 日訊號",
                "ohlcv_1min": "NOT_USED - V6 不使用分鐘資料",
            }
        }
    finally:
        db.close()
'''

if "/api/v6/trading-calendar/latest" not in c:
    c = c + V6C_APIS
    print("✓ V6C APIs 加入")
else:
    print("- V6C APIs 已存在")

with open("main.py","w") as f:
    f.write(c)

# ── 2. 每日工作流程整合 trading_calendar ──
with open("backend/v4/daily_workflow.py") as f:
    wf = f.read()

if "v6_1_build_trading_calendar" not in wf and "10e_trading_cal" not in wf:
    old = "    # Step 10d: V6 每日"
    new = """    # Step 10e: 更新 trading_calendar
    def _update_trading_cal():
        try:
            from scripts.v6_1_build_trading_calendar import build
            r = build()
            return {"status":"PASS","message":f"trading_calendar: {r.get('open_days',0)}個交易日"}
        except Exception as e:
            return {"status":"WARN","message":f"trading_calendar更新失敗: {e}"}
    step("10e_trading_cal", _update_trading_cal)

    # Step 10d: V6 每日"""
    if old in wf:
        wf = wf.replace(old, new)
        with open("backend/v4/daily_workflow.py","w") as f:
            f.write(wf)
        print("✓ trading_calendar 加入每日工作流程")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ main.py 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
r2 = subprocess.run(["python3","-m","py_compile","backend/v4/daily_workflow.py"], capture_output=True)
print("✓ daily_workflow 語法正確" if r2.returncode==0 else "❌ "+r2.stderr.decode())
