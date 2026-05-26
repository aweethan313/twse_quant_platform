"""backend/services/daily_review.py - 每日選股檢討書"""
from pathlib import Path
from datetime import date, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def generate_daily_review(signal_date: date = None, today: date = None) -> str | None:
    if signal_date is None:
        signal_date = date.today() - timedelta(days=1)
    if today is None:
        today = date.today()

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT DISTINCT ctp.code, ctp.name, ctp.reference_price,
                   ctp.target_price_1, ctp.stop_loss_price, ctp.candidate_pool_type,
                   o.close, o.change_pct,
                   ds.final_score, ds.risk_score, ds.momentum_score,
                   tdf.rsi14, tdf.distance_ma20, tdf.return_5d
            FROM candidate_trade_plans ctp
            LEFT JOIN ohlcv_daily o ON o.code=ctp.code AND o.trade_date=:today
            LEFT JOIN daily_scores ds ON ds.code=ctp.code AND ds.score_date=:signal
            LEFT JOIN technical_daily_features tdf ON tdf.code=ctp.code AND tdf.trade_date=:signal
            WHERE ctp.plan_date=:signal AND o.close IS NOT NULL
            GROUP BY ctp.code
            ORDER BY ds.final_score DESC
        """), {"today": str(today), "signal": str(signal_date)}).fetchall()
    finally:
        db.close()

    if not rows:
        logger.warning(f"[REVIEW] {signal_date}→{today} 無資料")
        return None

    total = len(rows)
    pos = sum(1 for r in rows if float(r[7] or 0) > 0)
    avg = sum(float(r[7] or 0) for r in rows) / total
    hit_target = sum(1 for r in rows if r[6] and float(r[6]) >= float(r[3] or 999))
    hit_stop = sum(1 for r in rows if r[6] and float(r[6]) <= float(r[4] or 0))

    lines = [
        "# 📋 每日選股檢討書",
        f"**資料日（T）：{signal_date}　→　交易日（T+1）：{today}**",
        "",
        "> ⚠️ 輔助看盤，所有交易須使用者自行確認。",
        "",
        "## 📊 整體成績",
        "",
        "| 項目 | 數值 |",
        "|------|------|",
        f"| 建議股數 | {total} 檔 |",
        f"| 正報酬 | {pos}/{total}（{pos/total*100:.0f}%）|",
        f"| 達目標價（+10%）| {hit_target} 檔 |",
        f"| 跌破停損（-8%）| {hit_stop} 檔 |",
        f"| 平均漲跌幅 | {avg:+.2f}% |",
        "",
        "## 🏆 前3強",
        "",
    ]

    sorted_rows = sorted(rows, key=lambda r: float(r[7] or 0), reverse=True)
    for r in sorted_rows[:3]:
        pct = float(r[7] or 0)
        lines.append(f"- **{r[1]}（{r[0]}）** {pct:+.2f}%")
    lines.append("")

    lines += [
        "## 🔍 個股明細",
        "",
        "| 股票 | 參考價 | 實際 | 漲跌 | RSI | 離MA20 | 問題 |",
        "|------|--------|------|------|-----|--------|------|",
    ]

    rsi_cnt = ma_cnt = 0
    for r in rows:
        pct = float(r[7] or 0)
        rsi = float(r[11] or 50)
        ma_dist = float(r[12] or 0)
        ret5d = float(r[13] or 0)
        icon = "✅" if pct > 3 else "🛑" if pct < -2 else "🟡"
        issues = []
        if rsi >= 85: issues.append(f"RSI{rsi:.0f}"); rsi_cnt += 1
        if ma_dist >= 12: issues.append(f"MA20+{ma_dist:.0f}%"); ma_cnt += 1
        if ret5d >= 12: issues.append(f"5日+{ret5d:.0f}%")
        lines.append(
            f"| {icon} {r[1]}({r[0]}) | {r[2]} | {r[6]} | {pct:+.2f}% "
            f"| {rsi:.0f} | {ma_dist:+.1f}% | {','.join(issues) or '正常'} |"
        )

    lines += ["", "## 💡 問題分析", ""]
    if rsi_cnt:
        lines.append(f"- ⚠️ RSI 過熱 {rsi_cnt} 檔（RSI≥85）→ 已改善：新增 RSI<85 硬性過濾")
    if ma_cnt:
        lines.append(f"- ⚠️ 追高 {ma_cnt} 檔（離MA20>12%）")
    if avg > 0:
        lines.append(f"- ✅ 平均正報酬 {avg:+.2f}%，方向判斷正確")
    lines += [
        "",
        "---",
        f"*生成：{date.today()}*",
    ]

    path = Path(f"data/reports/daily_review_{signal_date}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.success(f"[REVIEW] {signal_date}→{today} 完成，{total}檔 勝率{pos/total*100:.0f}%")
    return str(path)
