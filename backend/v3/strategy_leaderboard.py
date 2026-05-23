"""
backend/v3/strategy_leaderboard.py
V3-FIX-6：策略排名
V3-FIX-7：Paper Trading Research Log
"""
from __future__ import annotations
import json, math
from datetime import date, datetime, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


# ════════════════════════════════════════════════
# FIX-6: Strategy Leaderboard
# ════════════════════════════════════════════════

def _score_metric(value, good_direction="high", min_val=0, max_val=100) -> float:
    """將指標正規化到 0~100"""
    if value is None: return 50.0
    v = float(value)
    if good_direction == "high":
        return min(100, max(0, (v - min_val) / (max_val - min_val) * 100))
    else:  # low is better
        return min(100, max(0, (1 - (v - min_val) / (max_val - min_val)) * 100))


def compute_leaderboard(as_of_date: date = None) -> list[dict]:
    """計算所有策略的排名分數"""
    if as_of_date is None:
        as_of_date = date.today()
    db = SessionLocal()
    try:
        # 取所有策略
        strategies = db.execute(text("""
            SELECT id, name FROM strategy_accounts ORDER BY id
        """)).fetchall()

        results = []
        for sid, sname in strategies:
            metrics = _compute_strategy_metrics(sid, as_of_date, db)
            if not metrics:
                continue

            tr   = metrics["total_return"]
            mdd  = metrics["max_drawdown"]
            wr   = metrics["win_rate"]
            pf   = metrics["profit_factor"]
            tc   = metrics["trade_count"]
            stab = metrics["stability_score"]

            # 正規化各指標（0~100）
            tr_score   = _score_metric(tr,   "high", -50, 100)
            mdd_score  = _score_metric(mdd,  "low",  0,   50)
            wr_score   = _score_metric(wr,   "high", 30,  80)
            pf_score   = _score_metric(pf,   "high", 0.5, 3.0)
            stab_score = _score_metric(stab, "high", 0,   100)

            rank_score = (
                tr_score   * 0.35 +
                mdd_score  * 0.25 +
                wr_score   * 0.15 +
                pf_score   * 0.15 +
                stab_score * 0.10
            )

            # risk_label
            risk_label = "穩定"
            if mdd > 30:        risk_label = "回撤過大"
            elif tr > 50 and mdd > 20: risk_label = "高報酬高風險"
            elif tc < 5:        risk_label = "交易太少"
            elif wr > 80 and pf < 1.2: risk_label = "勝率高但盈虧比差"
            elif pf > 5 and tc < 10:   risk_label = "疑似過度擬合"

            # 年化報酬
            days_held = metrics.get("days_held", 365)
            ann_ret = ((1 + tr/100) ** (365/max(days_held,1)) - 1) * 100 if days_held > 0 else 0

            row = {
                "strategy_id": sid,
                "strategy_name": sname,
                "account_id": sid,
                "as_of_date": str(as_of_date),
                "total_return": round(tr, 2),
                "annualized_return": round(ann_ret, 2),
                "max_drawdown": round(mdd, 2),
                "win_rate": round(wr, 2),
                "profit_factor": round(pf, 2),
                "average_holding_days": round(metrics.get("avg_hold_days", 0), 1),
                "trade_count": tc,
                "stability_score": round(stab, 1),
                "overfit_score": 0,
                "strategy_rank_score": round(rank_score, 2),
                "risk_label": risk_label,
                "tr_score": round(tr_score,1),
                "mdd_score": round(mdd_score,1),
                "wr_score": round(wr_score,1),
                "pf_score": round(pf_score,1),
            }

            # 寫入 DB
            db.execute(text("""
                INSERT INTO strategy_leaderboard (
                    strategy_id, account_id, as_of_date, total_return,
                    annualized_return, max_drawdown, win_rate, profit_factor,
                    average_holding_days, trade_count, stability_score,
                    overfit_score, strategy_rank_score, risk_label
                ) VALUES (
                    :sid, :aid, :aod, :tr, :ar, :mdd, :wr, :pf,
                    :ahd, :tc, :ss, :os, :rs, :rl
                )
            """), {
                "sid": sid, "aid": sid, "aod": str(as_of_date),
                "tr": row["total_return"], "ar": row["annualized_return"],
                "mdd": row["max_drawdown"], "wr": row["win_rate"],
                "pf": row["profit_factor"], "ahd": row["average_holding_days"],
                "tc": tc, "ss": stab, "os": 0,
                "rs": rank_score, "rl": risk_label,
            })
            results.append(row)

        db.commit()
        results.sort(key=lambda x: x["strategy_rank_score"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1
        logger.success(f"[LEADERBOARD] {as_of_date} 計算完成，{len(results)} 個策略")
        return results
    except Exception as e:
        logger.error(f"[LEADERBOARD] 失敗: {e}")
        db.rollback()
        return []
    finally:
        db.close()


def _compute_strategy_metrics(strategy_id: int, as_of_date: date, db) -> dict:
    """計算策略績效指標"""
    try:
        # 取 equity curve
        equity_rows = db.execute(text("""
            SELECT date, total FROM equity_curve
            WHERE strategy_id=:sid AND date<=:d
            ORDER BY date
        """), {"sid": strategy_id, "d": str(as_of_date)}).fetchall()

        if len(equity_rows) < 2:
            return {}

        totals = [float(r[1]) for r in equity_rows]
        initial = totals[0]
        final   = totals[-1]
        total_return = (final / initial - 1) * 100 if initial > 0 else 0

        # Max drawdown
        peak = initial; mdd = 0
        for t in totals:
            if t > peak: peak = t
            dd = (peak - t) / peak * 100
            if dd > mdd: mdd = dd

        # 取交易記錄
        trades = db.execute(text("""
            SELECT action, pnl, trade_date,
                   (SELECT MIN(trade_date) FROM trade_logs tl2
                    WHERE tl2.strategy_id=:sid AND tl2.code=tl.code
                      AND tl2.action='BUY' AND tl2.trade_date<=tl.trade_date) as buy_date
            FROM trade_logs tl
            WHERE strategy_id=:sid AND action='SELL' AND trade_date<=:d AND pnl IS NOT NULL
        """), {"sid": strategy_id, "d": str(as_of_date)}).fetchall()

        if not trades:
            return {
                "total_return": total_return, "max_drawdown": mdd,
                "win_rate": 0, "profit_factor": 1.0, "trade_count": 0,
                "stability_score": 50, "avg_hold_days": 0,
                "days_held": len(equity_rows),
            }

        pnls = [float(t[1]) for t in trades if t[1] is not None]
        wins  = [p for p in pnls if p > 0]
        losses= [p for p in pnls if p <= 0]
        wr    = len(wins) / len(pnls) * 100 if pnls else 0
        gross_profit = sum(wins)
        gross_loss   = abs(sum(losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else (2.0 if gross_profit > 0 else 1.0)

        # 月度穩定性（月報酬標準差越小越穩定）
        monthly = {}
        for r in equity_rows:
            m = str(r[0])[:7]
            monthly[m] = float(r[1])

        monthly_rets = []
        months = sorted(monthly.keys())
        for i in range(1, len(months)):
            prev = monthly[months[i-1]]
            curr = monthly[months[i]]
            if prev > 0:
                monthly_rets.append((curr/prev-1)*100)

        stab = 100
        if len(monthly_rets) >= 2:
            avg = sum(monthly_rets)/len(monthly_rets)
            std = math.sqrt(sum((r-avg)**2 for r in monthly_rets)/len(monthly_rets))
            stab = max(0, 100 - std * 3)

        # 平均持倉天數
        hold_days = []
        for t in trades:
            if t[3]:
                try:
                    d1 = datetime.strptime(str(t[3]), "%Y-%m-%d")
                    d2 = datetime.strptime(str(t[2]), "%Y-%m-%d")
                    hold_days.append(abs((d2-d1).days))
                except: pass
        avg_hold = sum(hold_days)/len(hold_days) if hold_days else 0

        return {
            "total_return": total_return, "max_drawdown": mdd,
            "win_rate": wr, "profit_factor": pf, "trade_count": len(pnls),
            "stability_score": stab, "avg_hold_days": avg_hold,
            "days_held": len(equity_rows),
        }
    except Exception as e:
        logger.warning(f"[LEADERBOARD] S{strategy_id} metrics 失敗: {e}")
        return {}


def get_leaderboard(as_of_date: str = None) -> list[dict]:
    """取得最新排名（查 DB，無則重算）"""
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_leaderboard"
        params = {}
        if as_of_date:
            q += " WHERE as_of_date=:aod"
            params["aod"] = as_of_date
        else:
            q += " WHERE as_of_date=(SELECT MAX(as_of_date) FROM strategy_leaderboard)"
        q += " ORDER BY strategy_rank_score DESC"
        rows = db.execute(text(q), params).fetchall()
        if not rows:
            return compute_leaderboard(date.fromisoformat(as_of_date) if as_of_date else date.today())
        cols = ["id","strategy_id","account_id","as_of_date","total_return",
                "annualized_return","max_drawdown","win_rate","profit_factor",
                "average_holding_days","trade_count","stability_score",
                "overfit_score","strategy_rank_score","risk_label","created_at"]
        result = [dict(zip(cols, r)) for r in rows]
        for i, r in enumerate(result): r["rank"] = i+1
        return result
    finally:
        db.close()


# ════════════════════════════════════════════════
# FIX-7: Paper Trading Research Log
# ════════════════════════════════════════════════

def log_research(
    trade_date: date,
    code: str,
    suggested_action: str,
    suggested_price: float,
    scores: dict,
    market_regime: str = "neutral",
    strategy_id: int = None,
    account_id: int = None,
    name: str = None,
    actual_fill_price: float = None,
    reason: str = None,
):
    """記錄今日策略建議"""
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO paper_trading_research_log (
                date, strategy_id, account_id, code, name,
                suggested_action, suggested_price, actual_fill_price,
                reason_at_decision_time, market_regime_at_decision_time,
                score_components_json
            ) VALUES (
                :d, :sid, :aid, :code, :name,
                :sa, :sp, :afp, :reason, :mr, :scores
            )
        """), {
            "d": str(trade_date), "sid": strategy_id, "aid": account_id,
            "code": code, "name": name or code,
            "sa": suggested_action, "sp": suggested_price, "afp": actual_fill_price,
            "reason": reason or f"final_score={scores.get('final_score',0):.1f}",
            "mr": market_regime,
            "scores": json.dumps(scores, ensure_ascii=False),
        })
        db.commit()
    except Exception as e:
        logger.warning(f"[RESEARCH_LOG] 寫入失敗 {code}: {e}")
        db.rollback()
    finally:
        db.close()


def update_research_results(target_date: date = None):
    """
    更新 paper_trading_research_log 的後續表現
    自動補填 result_1d / result_3d / result_5d / result_10d
    """
    if target_date is None:
        target_date = date.today()

    db = SessionLocal()
    try:
        # 找需要更新的記錄
        rows = db.execute(text("""
            SELECT id, date, code, suggested_price
            FROM paper_trading_research_log
            WHERE result_5d IS NULL
              AND date <= date(:td, '-5 days')
        """), {"td": str(target_date)}).fetchall()

        updated = 0
        for row_id, log_date, code, ref_price in rows:
            if not ref_price: continue
            ref = float(ref_price)

            results = {}
            for n in [1, 3, 5, 10]:
                target_d = datetime.strptime(str(log_date), "%Y-%m-%d") + timedelta(days=n*1.5)
                close_row = db.execute(text("""
                    SELECT close FROM ohlcv_daily
                    WHERE code=:c AND trade_date > :d
                    ORDER BY trade_date LIMIT 1
                """), {"c": code, "d": log_date}).fetchone()

                n_row = db.execute(text(f"""
                    SELECT close FROM ohlcv_daily
                    WHERE code=:c AND trade_date >= :d
                    ORDER BY trade_date LIMIT 1 OFFSET {n-1}
                """), {"c": code, "d": log_date}).fetchone()

                if n_row:
                    ret = (float(n_row[0]) / ref - 1) * 100
                    results[f"result_{n}d"] = round(ret, 2)

            if results:
                # 判斷對錯
                r5 = results.get("result_5d", 0)
                log_row = db.execute(text(
                    "SELECT suggested_action FROM paper_trading_research_log WHERE id=:id"
                ), {"id": row_id}).fetchone()
                action = log_row[0] if log_row else "HOLD"

                was_correct = None
                error_type  = None
                if action == "BUY":
                    was_correct = 1 if r5 > 2 else 0
                    if not was_correct:
                        if r5 < -5:   error_type = "買進後跌破停損"
                        elif r5 < 0:  error_type = "技術突破失敗"
                        else:         error_type = "漲幅不如預期"

                set_parts = ", ".join(f"{k}=:{k}" for k in results)
                db.execute(text(f"""
                    UPDATE paper_trading_research_log
                    SET {set_parts},
                        was_decision_correct=:wdc,
                        error_type=:et,
                        updated_at=datetime('now','localtime')
                    WHERE id=:id
                """), {**results, "wdc": was_correct, "et": error_type, "id": row_id})
                updated += 1

        db.commit()
        logger.info(f"[RESEARCH_LOG] 更新 {updated} 筆後續表現")
        return updated
    except Exception as e:
        logger.error(f"[RESEARCH_LOG] 更新失敗: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def get_research_log(
    code: str = None,
    strategy_id: int = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 100,
) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM paper_trading_research_log WHERE 1=1"
        params = {}
        if code: q += " AND code=:code"; params["code"] = code
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        if date_from: q += " AND date>=:df"; params["df"] = date_from
        if date_to:   q += " AND date<=:dt"; params["dt"] = date_to
        q += " ORDER BY date DESC, id DESC LIMIT :limit"
        params["limit"] = limit

        rows = db.execute(text(q), params).fetchall()
        cols = ["id","date","strategy_id","account_id","code","name",
                "suggested_action","suggested_price","actual_fill_price",
                "reason_at_decision_time","market_regime_at_decision_time",
                "score_components_json","result_1d","result_3d","result_5d","result_10d",
                "was_decision_correct","error_type","created_at","updated_at"]
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            try: d["score_components_json"] = json.loads(d["score_components_json"] or "{}")
            except: d["score_components_json"] = {}
            result.append(d)
        return result
    finally:
        db.close()


def get_research_summary(strategy_id: int = None) -> dict:
    """取得研究日誌統計"""
    db = SessionLocal()
    try:
        q = "SELECT suggested_action, was_decision_correct, error_type FROM paper_trading_research_log WHERE result_5d IS NOT NULL"
        params = {}
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        rows = db.execute(text(q), params).fetchall()

        if not rows:
            return {"total": 0, "accuracy": 0, "error_types": {}}

        total = len(rows)
        correct = sum(1 for r in rows if r[1] == 1)
        accuracy = correct / total * 100 if total > 0 else 0

        error_types = {}
        for r in rows:
            if r[2]:
                error_types[r[2]] = error_types.get(r[2], 0) + 1

        buy_rows = [r for r in rows if r[0] == "BUY"]
        buy_acc = sum(1 for r in buy_rows if r[1] == 1) / len(buy_rows) * 100 if buy_rows else 0

        return {
            "total": total,
            "accuracy": round(accuracy, 1),
            "buy_accuracy": round(buy_acc, 1),
            "error_types": dict(sorted(error_types.items(), key=lambda x: x[1], reverse=True)),
        }
    finally:
        db.close()
