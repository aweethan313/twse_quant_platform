"""
backend/v3/watchlist_alerts.py
V3-FIX-13：早晨看盤提醒（不自動下單）
V3-FIX-14：候選股勝率追蹤
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

try:
    from config.capital_config import CAPITAL_CONFIG as CFG
except ImportError:
    class _Cfg:
        require_user_confirmation = True
        allow_auto_order = False
        default_stop_loss_pct = 0.08
        default_target_pct_1 = 0.10
    CFG = _Cfg()


# ════════════════════════════════════════════════
# FIX-13: Morning Watchlist Alerts
# ════════════════════════════════════════════════

def generate_morning_alerts(alert_date: date = None) -> list[dict]:
    """
    每天早上根據候選清單產生提醒（不自動下單）
    """
    if alert_date is None:
        alert_date = date.today()

    db = SessionLocal()
    try:
        # 取今日交易計畫
        plans = db.execute(text("""
            SELECT ctp.code, ctp.name, ctp.entry_price_low, ctp.entry_price_high,
                   ctp.target_price_1, ctp.target_price_2, ctp.stop_loss_price,
                   ctp.risk_reward_ratio, ctp.suggested_shares, ctp.suggested_amount,
                   ctp.invalid_buy_condition, ctp.final_plan_summary,
                   ds.final_action, ds.final_score, ds.risk_score, ds.stock_class
            FROM (
                SELECT code, MAX(id) as max_id FROM candidate_trade_plans
                WHERE plan_date=:d GROUP BY code
            ) latest
            JOIN candidate_trade_plans ctp ON ctp.id=latest.max_id
            LEFT JOIN daily_scores ds ON ds.code=ctp.code
              AND ds.score_date=(SELECT MAX(score_date) FROM daily_scores)
            ORDER BY
                CASE ds.stock_class
                    WHEN 'CORE_LARGE_CAP' THEN 1
                    WHEN 'LARGE_LIQUID' THEN 2
                    WHEN 'LIQUID_MOMENTUM' THEN 3
                    ELSE 4
                END,
                ds.final_score DESC
        """), {"d": str(alert_date)}).fetchall()

        # 大盤狀態
        ctx = db.execute(text("""
            SELECT trend_regime, breadth_score, summary
            FROM market_context_daily
            ORDER BY context_date DESC LIMIT 1
        """)).fetchone()
        market_summary = ctx[2] if ctx else "大盤資訊不足"
        breadth = float(ctx[1] or 50) if ctx else 50
        risk_level = "low" if breadth >= 60 else "high" if breadth <= 35 else "medium"

        alerts = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for p in plans:
            (code, name, el, eh, t1, t2, sl, rrr, shares, amount,
             invalid, summary, action, fs, rs, sc) = p

            # 決定 alert_type
            if invalid:
                alert_type = "DO_NOT_CHASE"
                warning = f"⚠️ {invalid}"
            elif action == "BUY" and (rrr or 0) >= 1.2:
                alert_type = "BUY_WATCH"
                warning = None
            elif action == "WATCH":
                alert_type = "HOLD_WATCH"
                warning = "等待更好進場點"
            else:
                alert_type = "HOLD_WATCH"
                warning = None

            if risk_level == "high":
                warning = (warning or "") + " ⚠️高風險市場，縮小部位"

            reason = f"最終分{fs:.1f} 風險分{rs:.1f} {sc or ''}"

            alert = {
                "alert_date": str(alert_date),
                "alert_time": now,
                "code": code,
                "name": name,
                "alert_type": alert_type,
                "entry_price_low": el,
                "entry_price_high": eh,
                "target_price_1": t1,
                "target_price_2": t2,
                "stop_loss_price": sl,
                "risk_reward_ratio": rrr,
                "suggested_shares": shares,
                "suggested_amount": amount,
                "alert_reason": reason,
                "warning_message": warning,
                "delivery_channel": "local_report",
                "delivery_status": "PENDING",
                "user_confirmation_status": "PENDING",
            }

            # 寫入 DB
            db.execute(text("""
                INSERT INTO watchlist_alerts (
                    alert_date, alert_time, code, name, alert_type,
                    entry_price_low, entry_price_high,
                    target_price_1, target_price_2, stop_loss_price,
                    risk_reward_ratio, suggested_shares, suggested_amount,
                    alert_reason, warning_message,
                    delivery_channel, delivery_status, user_confirmation_status
                ) VALUES (
                    :ad,:at,:code,:name,:atype,
                    :el,:eh,:t1,:t2,:sl,
                    :rrr,:ss,:sa,
                    :reason,:warning,
                    :dc,:ds,:ucs
                )
            """), {
                "ad": str(alert_date), "at": now, "code": code, "name": name,
                "atype": alert_type, "el": el, "eh": eh, "t1": t1, "t2": t2, "sl": sl,
                "rrr": rrr, "ss": shares, "sa": amount,
                "reason": reason, "warning": warning,
                "dc": "local_report", "ds": "GENERATED", "ucs": "PENDING",
            })
            alerts.append(alert)

        db.commit()

        # 產生 Markdown 報告
        _write_markdown_report(alerts, alert_date, market_summary, risk_level)
        _write_csv_report(alerts, alert_date)

        logger.success(f"[WATCHLIST] {alert_date} 生成 {len(alerts)} 個提醒")
        return alerts
    finally:
        db.close()


def _write_markdown_report(alerts: list, alert_date: date,
                            market_summary: str, risk_level: str):
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text

    path = Path(f"data/reports/morning_watchlist_alerts_{alert_date}.md")
    path.parent.mkdir(parents=True, exist_ok=True)

    rl_emoji = {"low":"🟢","medium":"🟡","high":"🔴"}.get(risk_level,"⚪")

    db = SessionLocal()
    # 0050 狀態
    etf_row = db.execute(_text("""
        SELECT close, change_pct FROM ohlcv_daily
        WHERE code='0050' ORDER BY trade_date DESC LIMIT 1
    """)).fetchone()
    etf_str = f"{etf_row[0]} ({'+' if etf_row[1]>=0 else ''}{etf_row[1]:.2f}%)" if etf_row else "N/A"

    # 核心大型股完整資料（含 MA20 距離）
    core_rows = db.execute(_text("""
        SELECT ds.code, sm.name, o.close, o.change_pct,
               ds.final_score, ds.candidate_score, ds.risk_score,
               ds.final_action, ds.stock_class, ds.entry_score
        FROM daily_scores ds
        LEFT JOIN stock_meta sm ON sm.code=ds.code
        LEFT JOIN ohlcv_daily o ON o.code=ds.code
          AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
        WHERE ds.score_date=(SELECT MAX(score_date) FROM daily_scores)
          AND ds.stock_class IN ('CORE_LARGE_CAP','ETF_CORE','ETF_INCOME')
          AND ds.candidate_score >= 48
        ORDER BY ds.candidate_score DESC LIMIT 15
    """)).fetchall()

    # MA20 查詢輔助
    def get_ma20_dist(code, close):
        row = db.execute(_text("""
            SELECT AVG(close) FROM (
                SELECT close FROM ohlcv_daily
                WHERE code=:c AND close IS NOT NULL
                ORDER BY trade_date DESC LIMIT 20
            )
        """), {"c": code}).scalar()
        if row and close:
            return (float(close) - float(row)) / float(row) * 100
        return None

    db.close()

    # 分類 alerts：CORE 股票移出一般買入清單
    core_codes = {r[0] for r in core_rows}
    buy_list   = [a for a in alerts if a["alert_type"]=="BUY_WATCH"   and a["code"] not in core_codes]
    watch_list = [a for a in alerts if a["alert_type"]=="HOLD_WATCH"  and a["code"] not in core_codes]
    avoid_list = [a for a in alerts if a["alert_type"]=="DO_NOT_CHASE"]

    lines = [
        f"# 📋 今日看盤提醒 {alert_date}",
        f"",
        f"> ⚠️ **輔助看盤用途，所有交易須使用者自行確認後執行，系統不自動下單。**",
        f"",
        f"## 📊 大盤概況",
        f"| 項目 | 狀態 |",
        f"|------|------|",
        f"| 市場風險 | {rl_emoji} {risk_level.upper()} |",
        f"| 0050 | {etf_str} |",
        f"| 夜盤摘要 | {market_summary} |",
        f"",
    ]

    # ── 核心大型股觀察 ──
    lines += ["## 🔵 核心大型股觀察", ""]
    lines += [
        "| 代號 | 名稱 | 收盤 | 漲跌 | 候選分 | 進場分 | 訊號 | 進場建議 |",
        "|------|------|------|------|--------|--------|------|----------|",
    ]
    for r in core_rows:
        code, name, close, pct, fs, cs, rs, fa, sc, es = r
        pct_str = f"{'+' if (pct or 0)>=0 else ''}{pct:.2f}%" if pct else "—"
        action_map = {"BUY":"✅買入","WATCH":"👀觀察","HOLD":"⏸持有","AVOID_CHASE":"⚠️不可追"}
        fa_str = action_map.get(fa, fa or "—")
        es_val = float(es or 50)
        cs_val = float(cs or 50)
        rs_val = float(rs or 30)

        # 進場建議
        if fa == "BUY" and es_val >= 55:
            entry_tip = f"✅ 可進場，進場分{es_val:.0f}"
        elif fa in ("WATCH","HOLD") and cs_val >= 60:
            entry_tip = f"👀 等回測 MA20 再進"
        elif rs_val >= 50:
            entry_tip = f"⚠️ 風險{rs_val:.0f}，縮小部位"
        elif sc == "ETF_CORE":
            entry_tip = "長期持有，不短線操作"
        else:
            entry_tip = "觀察中"

        lines.append(
            f"| {code} | {name or ''} | {close or '—'} | {pct_str} | "
            f"{cs_val:.1f} | {es_val:.1f} | {fa_str} | {entry_tip} |"
        )
    lines += ["", "---", ""]

    # ── 強勢買入候選（前8檔詳細，其餘列表）──
    if buy_list:
        lines += [f"## ✅ 買入觀察（{len(buy_list)} 檔）", ""]

        # 前 8 檔詳細
        for a in buy_list[:8]:
            lines += [
                f"### {a['code']} {a['name']}",
                f"",
                f"| 項目 | 數值 |",
                f"|------|------|",
                f"| 進場區間 | **{a['entry_price_low']} ～ {a['entry_price_high']}** |",
                f"| 目標價 1 | {a['target_price_1']}（+{CFG.default_target_pct_1*100:.0f}%）|",
                f"| 目標價 2 | {a['target_price_2']}（+{CFG.default_target_pct_2*100:.0f}%）|",
                f"| 停損價 | {a['stop_loss_price']}（-{CFG.default_stop_loss_pct*100:.0f}%）|",
                f"| 風報比 | {a['risk_reward_ratio']} |",
                f"| 建議股數 | {a['suggested_shares']} 股 / {a['suggested_amount']:,.0f} 元 |",
                f"| 類型 | {a.get('alert_reason','').split()[-1] if a.get('alert_reason') else '—'} |",
                f"",
            ]
            if a.get("warning_message"):
                lines += [f"> ⚠️ {a['warning_message']}", ""]

        # 第 9 檔以後只列清單
        if len(buy_list) > 8:
            lines += [f"**其他候選（{len(buy_list)-8} 檔，詳見 CSV）：**", ""]
            lines.append("| 代號 | 名稱 | 進場 | 目標 | 停損 |")
            lines.append("|------|------|------|------|------|")
            for a in buy_list[8:]:
                lines.append(
                    f"| {a['code']} | {a['name']} | "
                    f"{a['entry_price_low']}～{a['entry_price_high']} | "
                    f"{a['target_price_1']} | {a['stop_loss_price']} |"
                )
            lines.append("")

    # ── 觀察候選 ──
    if watch_list:
        lines += [f"## 👀 持續觀察（{len(watch_list)} 檔）", ""]
        lines += ["| 代號 | 名稱 | 進場區間 | 目標 | 停損 |",
                  "|------|------|----------|------|------|"]
        for a in watch_list:
            lines.append(
                f"| {a['code']} | {a['name']} | "
                f"{a['entry_price_low']}～{a['entry_price_high']} | "
                f"{a['target_price_1']} | {a['stop_loss_price']} |"
            )
        lines.append("")

    # ── 不可追 ──
    if avoid_list:
        lines += [f"## ⚠️ 今日不可追高（{len(avoid_list)} 檔）", ""]
        for a in avoid_list:
            lines.append(f"- **{a['code']} {a['name']}** — {a['warning_message'] or a['alert_reason']}")
        lines.append("")

    lines += [
        "---",
        f"*資金：總資產 {CFG.total_capital:,.0f} | 短線 {CFG.active_capital:,.0f} | 單筆最大虧損 {CFG.max_single_trade_loss_amount:,.0f}*",
        f"*模式：{CFG.mode} | 需確認：{CFG.require_user_confirmation} | 不自動下單*",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"[WATCHLIST] 報告輸出：{path}")



def _write_csv_report(alerts: list, alert_date: date):
    import csv
    path = Path(f"data/reports/morning_watchlist_alerts_{alert_date}.csv")
    if not alerts: return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "alert_date","code","name","alert_type",
            "entry_price_low","entry_price_high","target_price_1","target_price_2",
            "stop_loss_price","risk_reward_ratio","suggested_shares","suggested_amount",
            "alert_reason","warning_message"
        ])
        w.writeheader()
        w.writerows([{k: a.get(k,"") for k in w.fieldnames} for a in alerts])
    logger.info(f"[WATCHLIST] CSV 報告輸出：{path}")


def get_alerts(alert_date: str = None, code: str = None, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM watchlist_alerts WHERE 1=1"
        params = {}
        if alert_date: q += " AND alert_date=:ad"; params["ad"] = alert_date
        if code:       q += " AND code=:code";     params["code"] = code
        q += " ORDER BY alert_date DESC, id DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","alert_date","alert_time","code","name","alert_type",
                "entry_price_low","entry_price_high","target_price_1","target_price_2",
                "stop_loss_price","risk_reward_ratio","suggested_shares","suggested_amount",
                "alert_reason","warning_message","delivery_channel","delivery_status",
                "user_confirmation_status","created_at","updated_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


# ════════════════════════════════════════════════
# FIX-14: Candidate Accuracy Tracker
# ════════════════════════════════════════════════

def record_candidate_signal(
    signal_date: date,
    code: str,
    name: str,
    reference_price: float,
    entry_low: float,
    entry_high: float,
    target_1: float,
    stop_loss: float,
    target_2: float = None,
    strategy_id: int = None,
    candidate_pool_type: str = "NORMAL",
):
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT OR IGNORE INTO candidate_accuracy_tracker (
                signal_date, code, name, strategy_id, candidate_pool_type,
                reference_price, entry_price_low, entry_price_high,
                target_price_1, target_price_2, stop_loss_price
            ) VALUES (
                :sd,:code,:name,:sid,:cpt,
                :rp,:el,:eh,:t1,:t2,:sl
            )
        """), {
            "sd": str(signal_date), "code": code, "name": name,
            "sid": strategy_id, "cpt": candidate_pool_type,
            "rp": reference_price, "el": entry_low, "eh": entry_high,
            "t1": target_1, "t2": target_2 or target_1*1.05, "sl": stop_loss,
        })
        db.commit()
    except Exception as e:
        logger.warning(f"[ACCURACY] 記錄失敗 {code}: {e}")
        db.rollback()
    finally:
        db.close()


def update_accuracy_results(as_of_date: date = None):
    """更新所有候選股的後續表現"""
    if as_of_date is None:
        as_of_date = date.today()

    db = SessionLocal()
    updated = 0
    try:
        rows = db.execute(text("""
            SELECT id, signal_date, code, reference_price, target_price_1,
                   target_price_2, stop_loss_price
            FROM candidate_accuracy_tracker
            WHERE result_label IS NULL
              AND signal_date <= date(:d, '-3 days')
        """), {"d": str(as_of_date)}).fetchall()

        for row in rows:
            rid, sig_date, code, ref, t1, t2, sl = row
            if not ref: continue
            ref = float(ref)

            results = {}
            for n in [1, 3, 5, 10, 20]:
                # 取第 n 個交易日的最高/最低價
                hi_row = db.execute(text(f"""
                    SELECT MAX(high), MIN(low) FROM (
                        SELECT high, low FROM ohlcv_daily
                        WHERE code=:c AND trade_date > :sd
                        ORDER BY trade_date LIMIT {n}
                    )
                """), {"c": code, "sd": str(sig_date)}).fetchone()

                if hi_row and hi_row[0]:
                    max_r = (float(hi_row[0]) / ref - 1) * 100
                    min_r = (float(hi_row[1]) / ref - 1) * 100
                    results[f"max_return_{n}d"] = round(max_r, 2)
                    results[f"min_return_{n}d"] = round(min_r, 2)

            if not results:
                continue

            # 判斷是否達標
            hit_t1_5d  = 1 if results.get("max_return_5d", -99) >= float(t1 or 10) - ref/ref*100 else 0
            hit_t1_10d = 1 if results.get("max_return_10d",-99) >= float(t1 or 10) - ref/ref*100 else 0
            hit_sl_5d  = 1 if results.get("min_return_5d",  99) <= -(float(sl or 8))             else 0

            # 計算真正的 hit（用百分比）
            t1_ret = (float(t1)/ref - 1)*100 if t1 else 10
            sl_ret = (float(sl)/ref - 1)*100 if sl else -8
            hit_t1_5d  = 1 if results.get("max_return_5d",  -99) >= t1_ret else 0
            hit_t1_10d = 1 if results.get("max_return_10d", -99) >= t1_ret else 0
            hit_sl_5d  = 1 if results.get("min_return_5d",   99) <= sl_ret else 0

            # result_label
            if hit_t1_5d:
                label = "HIT_TARGET"
                error_type = None
            elif hit_sl_5d:
                label = "HIT_STOP_LOSS"
                r5 = results.get("min_return_5d", 0)
                if r5 < -15: error_type = "買進後跌破停損"
                else: error_type = "技術突破失敗"
            elif results.get("max_return_10d", 0) < 2:
                label = "SIDEWAYS"
                error_type = "技術突破失敗"
            else:
                label = "SIDEWAYS"
                error_type = None

            set_parts = ", ".join(f"{k}=:{k}" for k in results)
            db.execute(text(f"""
                UPDATE candidate_accuracy_tracker
                SET {set_parts},
                    hit_target_1_5d=:ht1_5, hit_target_1_10d=:ht1_10,
                    hit_stop_loss_5d=:hsl_5, result_label=:rl, error_type=:et,
                    updated_at=datetime('now','localtime')
                WHERE id=:id
            """), {**results, "ht1_5": hit_t1_5d, "ht1_10": hit_t1_10d,
                   "hsl_5": hit_sl_5d, "rl": label, "et": error_type, "id": rid})
            updated += 1

        db.commit()
        logger.info(f"[ACCURACY] 更新 {updated} 筆候選股後續表現")
        return updated
    finally:
        db.close()


def get_accuracy_stats(
    strategy_id: int = None,
    candidate_pool_type: str = None,
    start_date: str = None,
    end_date: str = None,
) -> dict:
    db = SessionLocal()
    try:
        q = "SELECT result_label, error_type, hit_target_1_5d, hit_stop_loss_5d FROM candidate_accuracy_tracker WHERE result_label IS NOT NULL"
        params = {}
        if strategy_id:        q += " AND strategy_id=:sid";       params["sid"] = strategy_id
        if candidate_pool_type: q += " AND candidate_pool_type=:cpt"; params["cpt"] = candidate_pool_type
        if start_date:         q += " AND signal_date>=:sd";        params["sd"] = start_date
        if end_date:           q += " AND signal_date<=:ed";        params["ed"] = end_date

        rows = db.execute(text(q), params).fetchall()
        if not rows:
            return {"total": 0, "hit_rate": 0, "stop_rate": 0, "error_types": {}}

        total = len(rows)
        hits  = sum(1 for r in rows if r[2] == 1)
        stops = sum(1 for r in rows if r[3] == 1)
        errors = {}
        for r in rows:
            if r[1]: errors[r[1]] = errors.get(r[1], 0) + 1

        return {
            "total": total,
            "hit_rate":  round(hits/total*100, 1),
            "stop_rate": round(stops/total*100, 1),
            "sideways_rate": round((total-hits-stops)/total*100, 1),
            "error_types": dict(sorted(errors.items(), key=lambda x: x[1], reverse=True)),
        }
    finally:
        db.close()


def get_accuracy_list(code: str = None, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM candidate_accuracy_tracker"
        params = {}
        if code: q += " WHERE code=:code"; params["code"] = code
        q += " ORDER BY signal_date DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","signal_date","code","name","strategy_id","candidate_pool_type",
                "reference_price","entry_price_low","entry_price_high",
                "target_price_1","target_price_2","stop_loss_price",
                "max_return_1d","max_return_3d","max_return_5d","max_return_10d","max_return_20d",
                "min_return_1d","min_return_3d","min_return_5d","min_return_10d","min_return_20d",
                "hit_target_1_5d","hit_target_1_10d","hit_target_2_10d",
                "hit_stop_loss_5d","hit_stop_loss_10d",
                "result_label","error_type","created_at","updated_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
