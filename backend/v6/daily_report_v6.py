"""backend/v6/daily_report_v6.py
V6-7 每日報告去重與專業化
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def generate_daily_report_v6(report_date: date = None) -> str:
    """產生去重、專業化的每日報告"""
    if report_date is None:
        report_date = date.today()

    db = SessionLocal()
    try:
        # ── 1. 大盤狀態 ──
        mkt = db.execute(text("""
            SELECT trend_regime, breadth_score, market_bias_score,
                   up_count, down_count, avg_change_pct, top_theme, summary
            FROM market_context_daily WHERE context_date=:d
        """), {"d": str(report_date)}).fetchone()

        # ── 2. 0050 benchmark ──
        bench = db.execute(text("""
            SELECT price, daily_return, cumulative_return, is_valid
            FROM benchmark_daily_equity
            WHERE benchmark_code='0050' AND snap_date=:d
        """), {"d": str(report_date)}).fetchone()

        # 檢查 benchmark 是否有效
        bench_warning = ""
        if bench and (bench[3] == 0 or bench[3] is None):
            bench_warning = " ⚠️ **benchmark 當日資料異常**"

        # ── 3. 策略績效（去重，每策略只出現一次）──
        strategies = db.execute(text("""
            SELECT DISTINCT a.id, a.name, eq.total_equity, a.initial_cash,
                   eq.daily_return, b.cumulative_return as bench_cum
            FROM strategy_accounts a
            LEFT JOIN equity_curve eq ON eq.account_id=a.id AND eq.snap_date=:d
            LEFT JOIN benchmark_daily_equity b ON b.benchmark_code='0050' AND b.snap_date=:d
            WHERE a.id >= 11
            ORDER BY eq.total_equity DESC NULLS LAST
        """), {"d": str(report_date)}).fetchall()

        # ── 4. Kill Switch（去重）──
        ks_rows = db.execute(text("""
            SELECT DISTINCT strategy_id, strategy_id as strategy_name, status, reason
            FROM strategy_kill_switch_status
            WHERE status != 'ACTIVE'
            ORDER BY strategy_id
        """)).fetchall()

        seen_ks = set()
        kill_switches = []
        for r in ks_rows:
            if r[0] not in seen_ks:
                seen_ks.add(r[0])
                kill_switches.append(r)

        # ── 5. 今日持倉（彙整所有帳戶）──
        positions = db.execute(text("""
            SELECT p.code, sm.name, SUM(p.lots) as total_lots,
                   AVG(p.avg_cost) as avg_cost,
                   o.close,
                   GROUP_CONCAT(DISTINCT a.name) as in_accounts
            FROM positions p
            LEFT JOIN stock_meta sm ON sm.code=p.code
            LEFT JOIN strategy_accounts a ON a.id=p.account_id
            LEFT JOIN ohlcv_daily o ON o.code=p.code AND o.trade_date=:d
            WHERE p.lots > 0
            GROUP BY p.code
            ORDER BY total_lots DESC
        """), {"d": str(report_date)}).fetchall()

        # ── 6. 明日候選股（去重，合併來源策略）──
        tomorrow = db.execute(text("""
            SELECT sdl.code, sm.name, ds.stock_class,
                   MAX(sdl.final_score) as max_score,
                   MIN(sdl.stop_loss) as stop_loss,
                   MAX(sdl.target_price) as target_price,
                   MAX(sdl.suggested_shares) as shares,
                   sdl.reference_price,
                   GROUP_CONCAT(DISTINCT sdl.account_id) as from_accounts,
                   MAX(sdl.reason_summary) as reason
            FROM strategy_decision_logs sdl
            LEFT JOIN stock_meta sm ON sm.code=sdl.code
            LEFT JOIN daily_scores ds ON ds.code=sdl.code AND ds.score_date=:d
            WHERE sdl.signal_date=:d AND sdl.action='BUY' AND sdl.is_blocked=0
            GROUP BY sdl.code
            ORDER BY max_score DESC
            LIMIT 10
        """), {"d": str(report_date)}).fetchall()

        # 取主題資訊
        theme_map = {}
        themes = db.execute(text("""
            SELECT ttd.leader_codes, ttd.theme
            FROM theme_trend_daily ttd WHERE ttd.context_date=:d
            ORDER BY ttd.score DESC LIMIT 5
        """), {"d": str(report_date)}).fetchall()
        for leaders, theme in themes:
            for code in str(leaders or "").split(","):
                code = code.strip()
                if code: theme_map[code] = theme

        # ── 7. 停損冷卻期 ──
        cooldowns = db.execute(text("""
            SELECT code, stock_name, strategy_name, cooldown_until, reason
            FROM strategy_cooldowns WHERE is_active=1
            ORDER BY cooldown_until
        """)).fetchall()

        # ── 開始組報告 ──
        lines = [
            f"# 📊 每日作戰報告 {report_date}",
            f"",
            f"## 一、大盤狀態",
        ]

        if mkt:
            risk_icon = "🔴" if "空" in str(mkt[0]) else "🟢" if "多" in str(mkt[0]) else "🟡"
            lines += [
                f"- 市場趨勢：{risk_icon} **{mkt[0] or '—'}**",
                f"- 廣度分數：{mkt[1] or 0:.1f} | 偏向分數：{mkt[2] or 0:.1f}",
                f"- 上漲：{mkt[3] or 0} 家 | 下跌：{mkt[4] or 0} 家 | 均漲跌：{mkt[5] or 0:+.2f}%",
                f"- 主線主題：{mkt[6] or '—'}",
            ]
        else:
            lines.append("- 今日大盤資料尚未更新")

        lines += ["", "## 二、0050 Benchmark"]
        if bench:
            lines += [
                f"- 今日價格：{bench[0] or '—'}",
                f"- 今日漲跌：{(bench[1] or 0):+.2f}%",
                f"- 累積報酬：{(bench[2] or 0):+.2f}%{bench_warning}",
            ]
        else:
            lines.append("- benchmark 資料尚未更新")

        lines += ["", "## 三、策略績效"]
        if strategies:
            bench_cum = float((bench[2] if bench else 0) or 0)
            lines.append(f"| 策略 | 總資產 | 累積 | Alpha | 今日 |")
            lines.append(f"|------|--------|------|-------|------|")
            for sid, sname, total, init, dr, _ in strategies:
                total_f = float(total or init or 200000)
                init_f = float(init or 200000)
                ret = (total_f / init_f - 1) * 100 if init_f else 0
                alpha = ret - bench_cum
                dr_f = float(dr or 0)
                beat = "✅" if alpha > 0 else "❌"
                lines.append(f"| {beat} {sname} | {total_f:,.0f} | {ret:+.2f}% | {alpha:+.2f}% | {dr_f:+.2f}% |")

        lines += ["", "## 四、Kill Switch 狀態"]
        if kill_switches:
            for sid, sname, status, reason in kill_switches:
                lines.append(f"- 🛑 **{sname}** [{status}] {reason or ''}")
        else:
            lines.append("- ✅ 所有策略正常，無 Kill Switch")

        lines += ["", "## 五、今日持倉"]
        if positions:
            lines.append(f"| 代號 | 名稱 | 總持股 | 均成本 | 現價 | 損益 | 所在帳戶 |")
            lines.append(f"|------|------|--------|--------|------|------|---------|")
            for code, name, lots, avg_cost, close, accounts in positions:
                close_f = float(close or 0)
                avg_f = float(avg_cost or 0)
                pnl = (close_f / avg_f - 1) * 100 if avg_f else 0
                pnl_icon = "📈" if pnl > 0 else "📉" if pnl < 0 else "➡️"
                lines.append(f"| {code} | {name or code} | {int(lots or 0)} | {avg_f:.2f} | {close_f:.2f} | {pnl_icon} {pnl:+.2f}% | {accounts or '—'} |")
        else:
            lines.append("- 目前無持倉")

        lines += ["", "## 六、明日候選股（T+1）"]
        seen_codes = set()
        if tomorrow:
            lines.append(f"| 代號 | 名稱 | 主題 | 分數 | 買入區間 | 停損 | 目標 | 建議股數 | 來源策略 |")
            lines.append(f"|------|------|------|------|---------|------|------|---------|---------|")
            for code, name, sc, score, sl, tp, shares, ref, accts, reason in tomorrow:
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                theme = theme_map.get(code, "—")
                buy_low = round(float(ref or 0) * 0.99, 2) if ref else "—"
                buy_high = round(float(ref or 0) * 1.01, 2) if ref else "—"
                lines.append(f"| **{code}** | {name or code} | {theme} | {float(score or 0):.1f} | {buy_low}~{buy_high} | {float(sl or 0):.2f} | {float(tp or 0):.2f} | {int(shares or 0)} | A{accts} |")
                lines.append(f"|  |  | *{reason or ''}* |  |  |  |  |  |  |")
        else:
            lines.append("- 今日無明日候選股建議")

        lines += ["", "## 七、停損冷卻期"]
        if cooldowns:
            for code, sname, strategy, until, reason in cooldowns:
                lines.append(f"- ❄️ **{code}** ({sname}) 冷卻至 {until}｜{reason or ''}（策略：{strategy or '—'}）")
        else:
            lines.append("- 無停損冷卻中的股票")

        lines += [
            "", "## 八、系統資料品質",
            f"- 最新交易日：{report_date}",
            f"- 0050 benchmark：{'⚠️ 有異常' if bench_warning else '✅ 正常'}",
            f"- 候選股數量：{len(seen_codes)} 檔",
            f"- 持倉股票：{len(positions)} 檔",
            "",
            "---",
            f"*報告產生時間：{date.today()} | V6 每日報告*"
        ]

        report = "\n".join(lines)

        # 儲存報告
        path = Path(f"data/reports/v6_daily_report_{report_date}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
        logger.success(f"[V6 REPORT] {report_date} 報告完成：{path}")
        return report

    finally:
        db.close()


if __name__ == "__main__":
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    print(generate_daily_report_v6(d))
