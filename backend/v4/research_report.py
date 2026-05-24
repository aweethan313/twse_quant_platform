"""
backend/v4/research_report.py
V4-11：完整研究報告輸出
"""
from __future__ import annotations
from datetime import date, datetime
from pathlib import Path
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def export_research_report(report_date: date = None) -> str:
    if report_date is None:
        report_date = date.today()

    db = SessionLocal()
    path = Path(f"data/reports/v4_research_report_{report_date}.md")
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# 📊 V4 每日研究報告 {report_date}",
        f"",
        f"> ⚠️ **輔助看盤用途。所有交易須使用者自行確認後執行，系統不自動下單。**",
        f"",
    ]

    # 1. 資料品質
    try:
        checks = db.execute(text("""
            SELECT check_type, status, severity, message FROM data_quality_checks
            WHERE check_date=:d ORDER BY severity DESC
        """), {"d": str(report_date)}).fetchall()
        if checks:
            fails = [c for c in checks if c[1]=="FAIL"]
            warns = [c for c in checks if c[1]=="WARN"]
            hs = max(0, 100 - len(fails)*20 - len(warns)*5)
            lines += [
                f"## 📊 資料品質",
                f"健康分：**{hs}/100** | ✅{sum(1 for c in checks if c[1]=='PASS')} ⚠️{len(warns)} ❌{len(fails)}",
                "",
            ]
            for c in fails:
                lines.append(f"- ❌ [{c[2]}] {c[0]}: {c[3]}")
            if fails: lines.append("")
    except Exception: pass

    # 2. 市場狀態
    try:
        ctx = db.execute(text("""
            SELECT trend_regime, breadth_score, summary, ai_theme_score
            FROM market_context_daily ORDER BY context_date DESC LIMIT 1
        """)).fetchone()
        if ctx:
            lines += [
                "## 📈 市場狀態", "",
                f"| 趨勢 | 廣度 | AI題材 | 摘要 |",
                f"|------|------|--------|------|",
                f"| {ctx[0]} | {ctx[1]:.0f} | {ctx[3]:.0f} | {ctx[2] or '—'} |",
                "",
            ]
    except Exception: pass

    # 3. 主題熱度
    try:
        themes = db.execute(text("""
            SELECT theme, score, code_count, summary FROM theme_trend_daily
            WHERE context_date=(SELECT MAX(context_date) FROM theme_trend_daily)
            ORDER BY score DESC LIMIT 10
        """)).fetchall()
        if themes:
            lines += ["## 🔥 主題熱度", ""]
            lines += ["| 主題 | 分數 | 股數 |", "|------|------|------|"]
            for t in themes:
                bar = "█" * int(float(t[1] or 0)/10)
                lines.append(f"| {t[0]} | {float(t[1]):.1f} {bar} | {t[2]} |")
            lines.append("")
    except Exception: pass

    # 4. 策略路由
    try:
        router = db.execute(text("""
            SELECT market_trend, risk_level, position_multiplier,
                   enabled_strategies, disabled_strategies, reason
            FROM strategy_router_decisions ORDER BY created_at DESC LIMIT 1
        """)).fetchone()
        if router:
            lines += [
                "## 🔀 策略路由", "",
                f"- 市場：{router[0]} | 風險：{router[1]} | 部位倍率：{float(router[2] or 0.65):.0%}",
                f"- 啟用：{router[3]} | 停用：{router[4]}",
                f"- 理由：{router[5]}",
                "",
            ]
    except Exception: pass

    # 5. Kill Switch
    try:
        ks = db.execute(text("""
            SELECT strategy_id, status, new_weight, reason, action_required
            FROM strategy_kill_switch_status
            WHERE check_date=(SELECT MAX(check_date) FROM strategy_kill_switch_status)
            ORDER BY strategy_id
        """)).fetchall()
        if ks:
            lines += ["## 🚨 策略 Kill Switch", ""]
            lines += ["| 策略 | 狀態 | 倍率 | 理由 |", "|------|------|------|------|"]
            for k in ks:
                ic = {"ACTIVE":"✅","REDUCED":"⚠️","PAUSED":"🛑","WATCHLIST":"👀"}.get(k[1],"•")
                lines.append(f"| S{k[0]} | {ic} {k[1]} | {float(k[2] or 1):.0%} | {(k[3] or '')[:50]} |")
            lines.append("")
    except Exception: pass

    # 6. 投組配置
    try:
        port = db.execute(text("""
            SELECT total_capital, cash, risk_level, reason, current_theme_exposure_json
            FROM portfolio_optimization_plans ORDER BY created_at DESC LIMIT 1
        """)).fetchone()
        if port:
            cash_r = float(port[1] or 0) / float(port[0] or 200000) * 100
            lines += [
                "## 💼 投組配置", "",
                f"- 總資產：{float(port[0] or 0):,.0f} | 現金：{float(port[1] or 0):,.0f}（{cash_r:.1f}%）",
                f"- 風險等級：{port[2]} | {(port[3] or '')[:80]}",
                "",
            ]
    except Exception: pass

    # 7. 明日看盤重點
    try:
        alerts = db.execute(text("""
            SELECT code, name, alert_type, entry_price_low, entry_price_high,
                   target_price_1, stop_loss_price, risk_reward_ratio, warning_message
            FROM watchlist_alerts WHERE alert_date=:d
            ORDER BY alert_type, id
        """), {"d": str(report_date)}).fetchall()

        buy_alerts = [a for a in alerts if a[2]=="BUY_WATCH"]
        avoid_alerts = [a for a in alerts if a[2]=="DO_NOT_CHASE"]

        if buy_alerts:
            lines += [f"## ✅ 明日買入觀察（{len(buy_alerts)} 檔，需使用者確認）", ""]
            lines += ["| 代號 | 名稱 | 進場區間 | 目標價 | 停損價 | 風報比 |",
                      "|------|------|----------|--------|--------|--------|"]
            for a in buy_alerts[:15]:
                lines.append(f"| {a[0]} | {a[1]} | {a[3]}～{a[4]} | {a[5]} | {a[6]} | {a[7]} |")
            lines.append("")

        if avoid_alerts:
            lines += [f"## ⚠️ 不可追高（{len(avoid_alerts)} 檔）", ""]
            for a in avoid_alerts:
                lines.append(f"- **{a[0]} {a[1]}** — {a[8] or '過熱，等回檔'}")
            lines.append("")
    except Exception: pass

    # 8. 情境壓力測試
    try:
        stress = db.execute(text("""
            SELECT scenario_name, estimated_return, risk_warning
            FROM scenario_stress_results
            WHERE test_date=(SELECT MAX(test_date) FROM scenario_stress_results)
            ORDER BY estimated_return LIMIT 5
        """)).fetchall()
        if stress:
            lines += ["## 🔥 壓力測試（最差情境）", ""]
            for s in stress:
                icon = "🔴" if float(s[1] or 0) < -5 else "⚠️" if float(s[1] or 0) < -2 else "🟡"
                lines.append(f"- {icon} **{s[0]}**：預估 {float(s[1] or 0):+.1f}%")
            lines.append("")
    except Exception: pass

    # 9. 核心大型股觀察
    try:
        core = db.execute(text("""
            SELECT ds.code, sm.name, o.close, o.change_pct,
                   ds.candidate_score, ds.final_action
            FROM daily_scores ds
            LEFT JOIN stock_meta sm ON sm.code=ds.code
            LEFT JOIN ohlcv_daily o ON o.code=ds.code
              AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
            WHERE ds.score_date=(SELECT MAX(score_date) FROM daily_scores)
              AND ds.stock_class='CORE_LARGE_CAP'
              AND ds.candidate_score >= 55
            ORDER BY ds.candidate_score DESC LIMIT 10
        """)).fetchall()
        if core:
            lines += ["## 🔵 核心大型股觀察", ""]
            lines += ["| 代號 | 名稱 | 收盤 | 漲跌 | 候選分 | 訊號 |",
                      "|------|------|------|------|--------|------|"]
            for r in core:
                pct = f"{'+' if (r[3] or 0)>=0 else ''}{r[3]:.2f}%" if r[3] else "—"
                fa = {"BUY":"✅買入","WATCH":"👀觀察","HOLD":"⏸持有"}.get(r[5],r[5] or "—")
                lines.append(f"| {r[0]} | {r[1] or ''} | {r[2] or '—'} | {pct} | {float(r[4] or 0):.1f} | {fa} |")
            lines.append("")
    except Exception: pass

    lines += [
        "---",
        f"**⚠️ 風險提醒**",
        f"- 所有 BUY_WATCH 均需使用者自行確認後才可下單",
        f"- 0050 為核心長期持有，不受短線策略影響",
        f"- 月報酬目標 10% 為高風險目標，風控優先於追求報酬",
        f"- 系統模式：輔助看盤 | 允許自動下單：否",
        f"",
        f"*報告生成：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ]

    db.close()
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.success(f"[REPORT] 完整研究報告輸出: {path}")
    return str(path)
