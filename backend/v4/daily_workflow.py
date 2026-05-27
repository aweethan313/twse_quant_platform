"""
backend/v4/daily_workflow.py
V4-3：每日工作流程一鍵執行
"""
from __future__ import annotations
import time
from datetime import date, datetime
from pathlib import Path
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def _log_step(db, run_date, step_name, status, message="", output_path="", duration=0):
    db.execute(text("""
        INSERT INTO daily_workflow_runs
            (run_date, run_time, workflow_version, step_name, status, message, output_path, duration_seconds)
        VALUES (:rd, :rt, 'V4', :step, :status, :msg, :out, :dur)
    """), {
        "rd": str(run_date),
        "rt": datetime.now().strftime("%H:%M:%S"),
        "step": step_name,
        "status": status,
        "msg": message,
        "out": output_path,
        "dur": round(duration, 2),
    })
    db.commit()
    icon = {"PASS":"✅","WARN":"⚠️","FAIL":"❌","SKIPPED":"⏭"}.get(status, "•")
    print(f"  {icon} [{status:7}] {step_name}: {message}")


def run_daily_workflow(target_date: date = None) -> dict:
    if target_date is None:
        target_date = date.today()

    print(f"\n{'='*55}")
    print(f"  V4 每日工作流程  {target_date}")
    print(f"{'='*55}")

    db = SessionLocal()
    step_results = []
    start_all = time.time()

    def step(name, fn, critical=False):
        t0 = time.time()
        try:
            result = fn()
            dur = time.time() - t0
            status = result.get("status", "PASS") if isinstance(result, dict) else "PASS"
            msg = result.get("message", str(result)[:100]) if isinstance(result, dict) else str(result)[:100]
            _log_step(db, target_date, name, status, msg, duration=dur)
            step_results.append({"step": name, "status": status})
            return result
        except Exception as e:
            dur = time.time() - t0
            _log_step(db, target_date, name, "FAIL", str(e)[:200], duration=dur)
            step_results.append({"step": name, "status": "FAIL", "error": str(e)})
            if critical:
                raise
            return None

    # Step 1: 更新每日資料
    def update_eod():
        from backend.services.latest_update import run_latest_update
        r = run_latest_update(str(target_date))
        return {"status": "PASS" if r.get("ok") else "WARN", "message": f"steps={len(r.get('steps',[]))}"}
    step("1_update_daily_data", update_eod)

    # Step 2: 更新主題熱度
    def update_themes():
        from backend.services.latest_update import update_theme_trends
        r = update_theme_trends(target_date)
        return {"status": "PASS" if r.get("ok") else "WARN",
                "message": f"更新{r.get('themes_updated',0)}個主題"}
    step("2_update_theme_trends", update_themes)

    # Step 3: 資料品質檢查
    def quality_check():
        from backend.v4.data_quality import run_data_quality_checks
        r = run_data_quality_checks(target_date)
        hs = r.get("overall_health", 0)
        status = "PASS" if hs >= 80 else "WARN" if hs >= 60 else "FAIL"
        return {"status": status, "message": f"健康分={hs:.0f} PASS={r.get('pass',0)} WARN={r.get('warn',0)} FAIL={r.get('fail',0)}"}
    step("3_data_quality_check", quality_check)

    # Step 4: 股市分類
    def classify():
        from backend.v4.market_sector import build_classification
        n = build_classification(target_date)
        return {"status": "PASS", "message": f"分類{n}檔"}
    step("4_market_sector_classification", classify)

    # Step 5: 策略路由
    def strategy_router():
        from backend.v3.strategy_router import compute_router
        r = compute_router(target_date)
        return {"status": "PASS",
                "message": f"市場={r.get('market_trend')} 啟用={r.get('enabled_strategies',[])}"}
    step("5_strategy_router", strategy_router)

    # Step 6: Strategy Kill Switch
    def kill_switch():
        from backend.v4.strategy_kill_switch import run_kill_switch
        results = run_kill_switch(target_date)
        paused = [r for r in results if r["status"] == "PAUSED"]
        return {"status": "WARN" if paused else "PASS",
                "message": f"{len(results)}個策略 暫停={len(paused)}"}
    step("6_strategy_kill_switch", kill_switch)

    # Step 7: 生成交易計畫
    def trade_plans():
        from backend.v3.candidate_trade_plans import generate_daily_plans
        plans = generate_daily_plans(target_date)
        return {"status": "PASS" if plans else "WARN",
                "message": f"生成{len(plans)}個交易計畫"}
    step("7_candidate_trade_plans", trade_plans)

    # Step 8: 早晨提醒
    def morning_alerts():
        from backend.v3.watchlist_alerts import generate_morning_alerts
        alerts = generate_morning_alerts(target_date)
        buy = sum(1 for a in alerts if a.get("alert_type") == "BUY_WATCH")
        path = f"data/reports/morning_watchlist_alerts_{target_date}.md"
        return {"status": "PASS", "message": f"生成{len(alerts)}個提醒 買入={buy}",
                "output": path}
    r8 = step("8_morning_watchlist_alerts", morning_alerts)

    # Step 9: 更新策略排名
    def leaderboard():
        from backend.v3.strategy_leaderboard import compute_leaderboard
        results = compute_leaderboard(target_date)
        return {"status": "PASS" if results else "WARN",
                "message": f"{len(results)}個策略排名"}
    step("9_strategy_leaderboard", leaderboard)

    # Step 10: 更新候選股追蹤
    def accuracy_update():
        from backend.v3.watchlist_alerts import update_accuracy_results
        n = update_accuracy_results(target_date)
        return {"status": "PASS", "message": f"更新{n}筆後續表現"}
    step("10_candidate_accuracy", accuracy_update)

    # Step 10.5: 每日檢討書
    def review():
        from backend.services.daily_review import generate_daily_review
        from datetime import timedelta
        path = generate_daily_review(target_date - timedelta(days=1), target_date)
        return {"status": "PASS" if path else "WARN",
                "message": f"檢討書: {path or '無前日資料'}"}
    step("10b_daily_review", review)

    # Step 10e: 更新 trading_calendar
    def _update_trading_cal():
        try:
            from scripts.v6_1_build_trading_calendar import build
            r = build()
            return {"status":"PASS","message":f"trading_calendar: {r.get('open_days',0)}個交易日"}
        except Exception as e:
            return {"status":"WARN","message":f"trading_calendar更新失敗: {e}"}
    step("10e_trading_cal", _update_trading_cal)

    # Step 10g: V7 每日
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

    # Step 10d: V6 每日
    def _v6_daily():
        try:
            from scripts.v6_detect_chip_anomalies import detect_chip_anomalies
            from scripts.v6_update_cooldowns import update_cooldowns
            from scripts.v6_update_strategy_health_scores import update_health_scores
            n = detect_chip_anomalies(target_date)
            update_cooldowns(target_date)
            update_health_scores()
            return {"status": "PASS", "message": f"V6: {n}個籌碼異動"}
        except Exception as e:
            return {"status": "WARN", "message": f"V6: {e}"}
    step("10d_v6_daily", _v6_daily)

    # Step 10c: V5 Paper Pipeline
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

    # Step 10f: V6 每日報告
    def _v6_report():
        try:
            from backend.v6.daily_report_v6 import generate_daily_report_v6
            generate_daily_report_v6(target_date)
            return {"status":"PASS","message":f"V6每日報告輸出"}
        except Exception as e:
            return {"status":"WARN","message":f"V6報告失敗: {e}"}
    step("10f_v6_report", _v6_report)

    # Step 11: 輸出日報告
    def export_report():
        path = export_daily_report(target_date, step_results)
        return {"status": "PASS" if path else "WARN",
                "message": f"報告輸出: {path}"}
    step("11_export_report", export_report)

    total_dur = time.time() - start_all
    pass_n = sum(1 for s in step_results if s["status"] == "PASS")
    fail_n = sum(1 for s in step_results if s["status"] == "FAIL")
    warn_n = sum(1 for s in step_results if s["status"] == "WARN")

    print(f"\n{'='*55}")
    print(f"  完成 {total_dur:.1f}秒 | PASS={pass_n} WARN={warn_n} FAIL={fail_n}")
    print(f"{'='*55}\n")

    db.close()
    return {"date": str(target_date), "pass": pass_n, "warn": warn_n,
            "fail": fail_n, "steps": step_results, "duration": round(total_dur, 1)}


def export_daily_report(target_date: date, step_results: list) -> str:
    """輸出每日 Markdown 研究報告"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text

    path = Path(f"data/reports/v4_daily_report_{target_date}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()

    lines = [
        f"# 📊 V4 每日研究報告 {target_date}",
        f"",
        f"> ⚠️ 輔助看盤用途，所有交易須使用者確認，系統不自動下單。",
        f"",
    ]

    # 工作流程狀態
    lines += ["## ⚙️ 工作流程", ""]
    for s in step_results:
        icon = {"PASS":"✅","WARN":"⚠️","FAIL":"❌","SKIPPED":"⏭"}.get(s["status"],"•")
        lines.append(f"- {icon} {s['step']}")
    lines.append("")

    # 大盤狀態
    try:
        ctx = db.execute(text("""
            SELECT trend_regime, breadth_score, summary FROM market_context_daily
            ORDER BY context_date DESC LIMIT 1
        """)).fetchone()
        if ctx:
            lines += [
                "## 📈 市場狀態", "",
                f"| 趨勢 | 廣度分 | 摘要 |",
                f"|------|--------|------|",
                f"| {ctx[0]} | {ctx[1]:.1f} | {ctx[2] or '—'} |",
                "",
            ]
    except Exception:
        pass

    # 主題熱度
    try:
        themes = db.execute(text("""
            SELECT theme, score, code_count FROM theme_trend_daily
            WHERE context_date=(SELECT MAX(context_date) FROM theme_trend_daily)
            ORDER BY score DESC LIMIT 8
        """)).fetchall()
        if themes:
            lines += ["## 🔥 主題熱度 TOP8", ""]
            lines += ["| 主題 | 分數 | 股票數 |", "|------|------|--------|"]
            for t in themes:
                bar = "█" * int(t[1] / 10)
                lines.append(f"| {t[0]} | {t[1]:.1f} {bar} | {t[2]} |")
            lines.append("")
    except Exception:
        pass

    # 策略路由
    try:
        router = db.execute(text("""
            SELECT market_trend, risk_level, position_multiplier, enabled_strategies, reason
            FROM strategy_router_decisions ORDER BY created_at DESC LIMIT 1
        """)).fetchone()
        if router:
            lines += [
                "## 🔀 策略路由", "",
                f"- 市場趨勢：{router[0]}",
                f"- 風險等級：{router[1]}",
                f"- 部位倍率：{router[2]:.0%}",
                f"- 啟用策略：{router[3]}",
                f"- 理由：{router[4]}",
                "",
            ]
    except Exception:
        pass

    # Kill Switch
    try:
        from backend.v4.strategy_kill_switch import get_kill_switch_status
        ks = get_kill_switch_status(str(target_date))
        if ks:
            lines += ["## 🚨 策略 Kill Switch", ""]
            lines += ["| 策略 | 狀態 | 倍率 | 理由 |", "|------|------|------|------|"]
            for k in ks:
                status_icon = {"ACTIVE":"✅","REDUCED":"⚠️","PAUSED":"🛑","WATCHLIST":"👀"}.get(k["status"],"•")
                lines.append(f"| S{k['strategy_id']} | {status_icon} {k['status']} | {k['new_weight']:.0%} | {(k['reason'] or '')[:40]} |")
            lines.append("")
    except Exception:
        pass

    # 今日看盤提醒
    try:
        alerts = db.execute(text("""
            SELECT code, name, alert_type, entry_price_low, entry_price_high,
                   target_price_1, stop_loss_price, risk_reward_ratio
            FROM watchlist_alerts WHERE alert_date=:d
            ORDER BY alert_type, id
        """), {"d": str(target_date)}).fetchall()
        if alerts:
            buy_alerts = [a for a in alerts if a[2] == "BUY_WATCH"]
            if buy_alerts:
                lines += [f"## ✅ 明日買入觀察（{len(buy_alerts)} 檔）", ""]
                lines += ["| 代號 | 名稱 | 進場 | 目標 | 停損 | 風報比 |",
                          "|------|------|------|------|------|--------|"]
                for a in buy_alerts:
                    lines.append(
                        f"| {a[0]} | {a[1]} | {a[3]}～{a[4]} | {a[5]} | {a[6]} | {a[7]} |"
                    )
                lines.append("")
    except Exception:
        pass

    # 風險提醒
    lines += [
        "## ⚠️ 風險提醒", "",
        "- 所有 BUY_WATCH 均需使用者自行確認後才可下單",
        "- 0050 為核心長期持有，不受短線策略影響",
        "- 月報酬目標 10% 為高風險目標，風控優先於追求報酬",
        "",
        "---",
        f"*報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        f"*系統模式：輔助看盤 | 允許自動下單：否 | 需使用者確認：是*",
    ]

    db.close()
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"[WORKFLOW] 日報告輸出: {path}")
    return str(path)


def get_workflow_runs(run_date: str = None, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM daily_workflow_runs WHERE 1=1"
        params = {}
        if run_date:
            q += " AND run_date=:rd"
            params["rd"] = run_date
        q += " ORDER BY id DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","run_date","run_time","workflow_version","step_name",
                "status","message","output_path","duration_seconds","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
