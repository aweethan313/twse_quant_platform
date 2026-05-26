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
        # 取市場背景
        mkt = db.execute(text("""
            SELECT trend_regime, breadth_score, summary
            FROM market_context_daily WHERE context_date=:d
        """), {"d": str(today)}).fetchone()

        etf = db.execute(text("""
            SELECT close, change_pct FROM ohlcv_daily
            WHERE code='0050' AND trade_date=:d
        """), {"d": str(today)}).fetchone()

        rows = db.execute(text("""
            SELECT DISTINCT ctp.code, ctp.name, ctp.reference_price,
                   ctp.target_price_1, ctp.stop_loss_price, ctp.candidate_pool_type,
                   o.close, o.change_pct,
                   ds.final_score, ds.risk_score, ds.momentum_score,
                   tdf.rsi14, tdf.distance_ma20, tdf.return_5d, tdf.volatility_20d
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

        # Fallback：無 candidate_trade_plans 時，從 daily_scores 重建
        if not rows:
            rows = db.execute(text("""
                SELECT DISTINCT ds.code, sm.name,
                       o_sig.close, o_sig.close*1.10, o_sig.close*0.92, ds.stock_class,
                       o_rev.close, o_rev.change_pct,
                       ds.final_score, ds.risk_score, ds.momentum_score,
                       tdf.rsi14, tdf.distance_ma20, tdf.return_5d, tdf.volatility_20d
                FROM daily_scores ds
                LEFT JOIN stock_meta sm ON sm.code=ds.code
                LEFT JOIN ohlcv_daily o_sig ON o_sig.code=ds.code AND o_sig.trade_date=:signal
                LEFT JOIN ohlcv_daily o_rev ON o_rev.code=ds.code AND o_rev.trade_date=:today
                LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code AND tdf.trade_date=:signal
                WHERE ds.score_date=:signal
                  AND ds.final_action IN ('BUY','WATCH')
                  AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','SPECULATIVE_HOT','NORMAL')
                  AND o_sig.close IS NOT NULL AND o_rev.close IS NOT NULL
                  AND o_sig.close >= 10
                  AND (tdf.rsi14 IS NULL OR tdf.rsi14 < 85)
                ORDER BY CASE ds.stock_class WHEN 'CORE_LARGE_CAP' THEN 1
                         WHEN 'LARGE_LIQUID' THEN 2 ELSE 3 END,
                         ds.final_score DESC LIMIT 15
            """), {"today": str(today), "signal": str(signal_date)}).fetchall()

    if not rows:
        logger.warning(f"[REVIEW] {signal_date}→{today} 無資料")
        return None

    total = len(rows)
    pos = sum(1 for r in rows if float(r[7] or 0) > 0)
    avg = sum(float(r[7] or 0) for r in rows) / total
    hit_t = sum(1 for r in rows if r[6] and float(r[6]) >= float(r[3] or 999))
    hit_s = sum(1 for r in rows if r[6] and float(r[6]) <= float(r[4] or 0))

    mkt_trend = mkt[0] if mkt else "unknown"
    mkt_breadth = float(mkt[1] or 50) if mkt else 50
    etf_pct = float(etf[1] or 0) if etf else 0
    mkt_day = "多頭日" if etf_pct > 1 else "空頭日" if etf_pct < -1 else "震盪日"

    # 分類股票
    stars    = [r for r in rows if float(r[7] or 0) >= 5]
    gains    = [r for r in rows if 0 < float(r[7] or 0) < 5]
    flat     = [r for r in rows if -2 <= float(r[7] or 0) <= 0]
    losers   = [r for r in rows if float(r[7] or 0) < -2]

    def diagnose(r):
        """診斷一檔股票為什麼漲/跌"""
        pct = float(r[7] or 0)
        rsi = float(r[11] or 50)
        ma_dist = float(r[12] or 0)
        ret5d = float(r[13] or 0)
        vol = float(r[14] or 0)
        risk = float(r[9] or 30)
        pool = r[5] or ""
        reasons = []

        if pct > 5:
            if ret5d >= 10: reasons.append("強勢動能延續，5日已漲勢帶動")
            if pool == "CORE_LARGE_CAP": reasons.append("核心大型股，跟隨大盤上漲")
            else: reasons.append("題材/動能強勢，短線爆發")
        elif pct > 0:
            if pool == "CORE_LARGE_CAP": reasons.append("核心大型股穩定跟漲")
            elif abs(pct) < 1: reasons.append("量能不足，小漲整理")
            else: reasons.append("溫和上漲，未達強勢")
        elif pct >= -2:
            if etf_pct < -0.5: reasons.append(f"大盤跌日（0050 {etf_pct:.1f}%），被拖累")
            if rsi >= 70: reasons.append(f"RSI={rsi:.0f}仍偏高，短線獲利回吐")
            if ma_dist >= 8: reasons.append(f"離MA20={ma_dist:.0f}%，技術面過度延伸")
        else:
            if etf_pct < -1: reasons.append(f"大盤重跌日（0050 {etf_pct:.1f}%），整體回檔")
            if rsi >= 80: reasons.append(f"RSI={rsi:.0f}嚴重過熱，強制回落")
            if risk >= 50: reasons.append(f"風險分={risk:.0f}偏高，下跌放大")
            if pool not in ("CORE_LARGE_CAP","LARGE_LIQUID"): reasons.append("中小型股，流動性差時優先殺")

        return "、".join(reasons) if reasons else "無明顯單一原因"

    # 學習重點
    lessons = []
    if etf_pct < -0.5:
        lessons.append(f"📉 **大盤影響**：今日 0050 {etf_pct:.1f}%（{mkt_day}），整體不利多頭。建議：大盤跌日縮手，只留核心大型股觀察。")
    if hit_s > 0:
        lessons.append(f"🛑 **{hit_s} 檔跌破停損**：停損設定有效，但說明進場時機或選股需改善。")
    rsi_issues = sum(1 for r in losers if float(r[11] or 50) >= 75)
    if rsi_issues:
        lessons.append(f"⚠️ **RSI 過熱問題**：{rsi_issues} 檔虧損股的 RSI 偏高，高位追入仍有風險。建議 RSI<75 才進場。")
    core_wins = [r for r in rows if r[5]=='CORE_LARGE_CAP' and float(r[7] or 0) > 0]
    if core_wins:
        lessons.append(f"✅ **核心大型股**：{len(core_wins)} 檔正報酬，波動小、穩定性佳，應優先配置。")
    if avg > 1:
        lessons.append(f"✅ **整體方向正確**：平均 {avg:+.2f}%，選股邏輯有效。")
    elif avg < 0:
        lessons.append(f"🔴 **整體虧損**：平均 {avg:+.2f}%，需檢視大盤環境再決定是否進場。")

    lines = [
        f"# 📋 選股檢討 {signal_date} → {today}",
        "",
        f"> **市場環境**：{mkt_trend} | 廣度分 {mkt_breadth:.0f} | 0050 {etf_pct:+.2f}%（{mkt_day}）",
        "",
        "---",
        "",
        "## 🎯 成績單",
        "",
        f"| 指標 | 數值 |",
        f"|------|------|",
        f"| 建議股數 | **{total}** 檔 |",
        f"| 正報酬 | **{pos}/{total}**（{pos/total*100:.0f}%）|",
        f"| 達目標（+10%）| **{hit_t}** 檔 |",
        f"| 觸停損（-8%）| **{hit_s}** 檔 |",
        f"| 平均漲跌幅 | **{avg:+.2f}%** |",
        "",
    ]

    if stars:
        lines += ["## 🌟 強勢股（漲幅 >5%）", ""]
        for r in sorted(stars, key=lambda x: float(x[7] or 0), reverse=True):
            pct = float(r[7] or 0)
            lines += [
                f"### ✅ {r[1]}（{r[0]}）{pct:+.2f}%",
                f"**為什麼漲**：{diagnose(r)}",
                f"參考價 {r[2]} → 實際收盤 {r[6]}",
                "",
            ]

    if losers:
        lines += ["## 🔍 需要檢討（跌幅 >2%）", ""]
        for r in sorted(losers, key=lambda x: float(x[7] or 0)):
            pct = float(r[7] or 0)
            rsi = float(r[11] or 50)
            ma_dist = float(r[12] or 0)
            lines += [
                f"### 🛑 {r[1]}（{r[0]}）{pct:+.2f}%",
                f"**為什麼跌**：{diagnose(r)}",
                f"參考價 {r[2]} → 實際收盤 {r[6]} | RSI={rsi:.0f} | 離MA20={ma_dist:+.1f}%",
                "",
            ]

    if flat or gains:
        lines += ["## 🟡 持平/小漲（-2% ～ +5%）", ""]
        lines += ["| 股票 | 漲跌 | 原因 |", "|------|------|------|"]
        for r in sorted(gains + flat, key=lambda x: float(x[7] or 0), reverse=True):
            pct = float(r[7] or 0)
            icon = "🟢" if pct > 0 else "🔴"
            lines.append(f"| {icon} {r[1]}({r[0]}) | {pct:+.2f}% | {diagnose(r)} |")
        lines.append("")

    # ── 深度分析 ──
    # 1. vs 0050
    etf_pct_val = float(etf[1] or 0) if etf else 0
    alpha = avg - etf_pct_val

    # 2. 類型勝率
    type_stats = {}
    for r in rows:
        t = r[5] or "OTHER"
        pct_r = float(r[7] or 0)
        if t not in type_stats:
            type_stats[t] = {"total":0,"win":0,"sum":0}
        type_stats[t]["total"] += 1
        type_stats[t]["sum"] += pct_r
        if pct_r > 0:
            type_stats[t]["win"] += 1

    # 3. RSI 區間分析
    rsi_buckets = {"30-50":[],"50-65":[],"65-80":[]}
    for r in rows:
        rsi_v = float(r[11] or 50)
        pct_r = float(r[7] or 0)
        if rsi_v < 50: rsi_buckets["30-50"].append(pct_r)
        elif rsi_v < 65: rsi_buckets["50-65"].append(pct_r)
        else: rsi_buckets["65-80"].append(pct_r)

    # 4. 如果只選 RSI<70 的結果
    filtered = [r for r in rows if float(r[11] or 50) < 70]
    filt_avg = sum(float(r[7] or 0) for r in filtered)/len(filtered) if filtered else 0
    filt_win = sum(1 for r in filtered if float(r[7] or 0) > 0)

    # 5. 資金管理建議
    deploy_pct = 0
    if etf_pct_val > 1 and mkt_breadth > 60:
        deploy_pct = 70
        deploy_msg = "大盤強勢日，可投入可用資金的 70%"
    elif etf_pct_val > 0:
        deploy_pct = 50
        deploy_msg = "大盤小漲，建議投入可用資金的 50%，保留彈藥"
    elif etf_pct_val > -1:
        deploy_pct = 30
        deploy_msg = "大盤持平偏弱，建議僅投入 30%，等更好時機"
    else:
        deploy_pct = 0
        deploy_msg = "大盤下跌日，建議觀望，保留彈藥等核心股回檔加碼"

    TYPE_ZH = {
        "CORE_LARGE_CAP": "核心大型",
        "LARGE_LIQUID": "中大型",
        "LIQUID_MOMENTUM": "強勢動能",
        "SPECULATIVE_HOT": "題材投機",
        "ETF_CORE": "ETF",
        "OTHER": "其他",
    }

    lines += [
        "---",
        "",
        "## 💡 今日學習",
        "",
    ]
    for l in lessons:
        lines.append(l)
        lines.append("")

    lines += [
        "---",
        "",
        "## 📊 深度分析",
        "",
        "### vs 大盤比較",
        "",
        f"| 指標 | 數值 |",
        f"|------|------|",
        f"| 0050 當日漲跌 | {etf_pct_val:+.2f}%（{mkt_day}）|",
        f"| 本次選股平均 | {avg:+.2f}% |",
        f"| 超額報酬（Alpha）| **{alpha:+.2f}%** {'✅ 跑贏大盤' if alpha > 0 else '❌ 跑輸大盤'} |",
        "",
        "### 類型勝率分析",
        "",
        "| 類型 | 股數 | 勝率 | 平均報酬 | 建議 |",
        "|------|------|------|---------|------|",
    ]
    for t, s in sorted(type_stats.items(), key=lambda x: x[1]["sum"]/x[1]["total"] if x[1]["total"] else 0, reverse=True):
        if s["total"] == 0: continue
        t_avg = s["sum"]/s["total"]
        t_win = s["win"]/s["total"]*100
        suggest = "✅ 優先" if t_avg > 1 and t_win >= 60 else "⚠️ 謹慎" if t_avg < 0 else "🟡 普通"
        lines.append(f"| {TYPE_ZH.get(t,t)} | {s['total']} | {t_win:.0f}% | {t_avg:+.2f}% | {suggest} |")

    lines += [
        "",
        "### RSI 區間績效",
        "",
        "| RSI 區間 | 股數 | 平均報酬 | 建議 |",
        "|---------|------|---------|------|",
    ]
    for bucket, vals in rsi_buckets.items():
        if not vals: continue
        b_avg = sum(vals)/len(vals)
        lines.append(f"| RSI {bucket} | {len(vals)} | {b_avg:+.2f}% | {'✅' if b_avg > 0.5 else '⚠️'} |")

    lines += [
        "",
        "### 如果只選 RSI < 70",
        "",
        f"| 指標 | 全部選股 | RSI<70 篩選後 |",
        f"|------|---------|-------------|",
        f"| 股數 | {total} | {len(filtered)} |",
        f"| 勝率 | {pos/total*100:.0f}% | {filt_win/len(filtered)*100:.0f}% |",
        f"| 平均報酬 | {avg:+.2f}% | {filt_avg:+.2f}% |",
        "",
        "### 💰 資金管理建議",
        "",
        f"> **今日建議：{deploy_msg}**",
        f"> 可用資金 10 萬 × {deploy_pct}% = **{100000*deploy_pct//100:,} 元** 可動用",
        f"> 剩餘 **{100000*(100-deploy_pct)//100:,} 元** 保留，等核心股（0050/國泰金）回檔時加碼",
        "",
        "---",
        f"*生成時間：{date.today()} | 僅供輔助看盤，所有交易須自行確認*",
    ]

    path = Path(f"data/reports/daily_review_{signal_date}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.success(f"[REVIEW] {signal_date}→{today} 完成，{total}檔 勝率{pos/total*100:.0f}% 平均{avg:+.2f}%")
    return str(path)
