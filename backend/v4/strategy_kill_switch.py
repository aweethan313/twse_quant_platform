"""
backend/v4/strategy_kill_switch.py
V4-9：策略降權/停用機制
"""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def _get_recent_trades(strategy_id: int, n: int, db) -> list[dict]:
    rows = db.execute(text("""
        SELECT code, direction, lots, price, pnl, ts
        FROM trade_logs
        WHERE account_id=:sid AND pnl IS NOT NULL
        ORDER BY ts DESC LIMIT :n
    """), {"sid": strategy_id, "n": n}).fetchall()
    return [{"code": r[0], "direction": r[1], "lots": r[2],
             "price": r[3], "pnl": float(r[4] or 0)} for r in rows]


def run_kill_switch(check_date: date = None) -> list[dict]:
    if check_date is None:
        check_date = date.today()

    db = SessionLocal()
    results = []

    try:
        strategies = db.execute(text(
            "SELECT id, name FROM strategy_accounts ORDER BY id"
        )).fetchall()

        for sid, sname in strategies:
            trades_10 = _get_recent_trades(sid, 10, db)
            trades_20 = _get_recent_trades(sid, 20, db)

            sells_10 = [t for t in trades_10 if t["direction"] == "sell"]
            sells_20 = [t for t in trades_20 if t["direction"] == "sell"]

            win_rate_10 = sum(1 for t in sells_10 if t["pnl"] > 0) / len(sells_10) if sells_10 else 0.5
            win_rate_20 = sum(1 for t in sells_20 if t["pnl"] > 0) / len(sells_20) if sells_20 else 0.5

            # 最近 equity curve
            eq_rows = db.execute(text("""
                SELECT snap_date, total_equity FROM equity_curve
                WHERE account_id=:sid
                ORDER BY snap_date DESC LIMIT 30
            """), {"sid": sid}).fetchall()

            recent_return = 0.0
            recent_mdd = 0.0
            if len(eq_rows) >= 2:
                latest = float(eq_rows[0][1] or 200000)
                oldest = float(eq_rows[-1][1] or 200000)
                recent_return = (latest / oldest - 1) * 100 if oldest > 0 else 0
                peak = oldest
                for r in reversed(eq_rows):
                    v = float(r[1] or 0)
                    if v > peak: peak = v
                    dd = (peak - v) / peak * 100 if peak > 0 else 0
                    if dd > recent_mdd: recent_mdd = dd

            # Walk-forward overfit
            wf = db.execute(text("""
                SELECT AVG(overfit_score) FROM walk_forward_results
                WHERE strategy_id=:sid
            """), {"sid": sid}).scalar() or 0
            overfit_score = float(wf)

            # 決定 kill switch 狀態
            reasons = []
            status = "ACTIVE"
            prev_w = 1.0
            new_w = 1.0

            if win_rate_10 < 0.35 and len(sells_10) >= 5:
                reasons.append(f"近10筆勝率{win_rate_10*100:.0f}%<35%")
                status = "REDUCED"
                new_w = 0.5

            if recent_mdd > 15:
                reasons.append(f"近期最大回撤{recent_mdd:.1f}%>15%")
                status = "REDUCED" if status == "ACTIVE" else status
                new_w = min(new_w, 0.5)

            if recent_mdd > 25:
                reasons.append(f"回撤{recent_mdd:.1f}%嚴重")
                status = "PAUSED"
                new_w = 0

            if overfit_score > 40:
                reasons.append(f"Walk-forward過擬合分{overfit_score:.0f}>40")
                status = "WATCHLIST" if status == "ACTIVE" else status
                new_w = min(new_w, 0.7)

            if len(sells_20) < 2:
                reasons.append(f"近20期僅{len(sells_20)}筆交易，訊號可能失效")
                status = "WATCHLIST" if status == "ACTIVE" else status

            action = None
            if status == "PAUSED":
                action = "立即停止新交易，檢視策略邏輯"
            elif status == "REDUCED":
                action = f"建議部位縮減至{int(new_w*100)}%，持續觀察"
            elif status == "WATCHLIST":
                action = "列入觀察，下周重新評估"

            row = {
                "check_date": str(check_date),
                "strategy_id": sid,
                "strategy_name": sname,
                "account_id": sid,
                "status": status,
                "previous_weight": prev_w,
                "new_weight": new_w,
                "reason": "；".join(reasons) if reasons else "績效正常",
                "recent_return": round(recent_return, 2),
                "recent_win_rate": round(win_rate_10 * 100, 1),
                "recent_max_drawdown": round(recent_mdd, 2),
                "trade_count": len(sells_20),
                "overfit_score": round(overfit_score, 1),
                "action_required": action,
            }

            db.execute(text("""
                INSERT INTO strategy_kill_switch_status
                    (check_date, strategy_id, account_id, status,
                     previous_weight, new_weight, reason,
                     recent_return, recent_win_rate, recent_max_drawdown,
                     trade_count, overfit_score, action_required)
                VALUES (:cd,:sid,:aid,:status,:pw,:nw,:reason,
                        :rr,:wr,:mdd,:tc,:os,:action)
            """), {
                "cd": str(check_date), "sid": sid, "aid": sid,
                "status": status, "pw": prev_w, "nw": new_w,
                "reason": row["reason"], "rr": recent_return,
                "wr": win_rate_10 * 100, "mdd": recent_mdd,
                "tc": len(sells_20), "os": overfit_score,
                "action": action,
            })
            results.append(row)
            logger.info(f"[KILL] S{sid} {sname}: {status} {row['reason']}")

        db.commit()
        return results
    except Exception as e:
        logger.error(f"[KILL] 失敗: {e}")
        db.rollback()
        return []
    finally:
        db.close()


def get_kill_switch_status(check_date: str = None) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_kill_switch_status WHERE 1=1"
        params = {}
        if check_date:
            q += " AND check_date=:cd"
            params["cd"] = check_date
        else:
            q += " AND check_date=(SELECT MAX(check_date) FROM strategy_kill_switch_status)"
        q += " ORDER BY strategy_id"
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","check_date","strategy_id","account_id","status",
                "previous_weight","new_weight","reason","recent_return",
                "recent_win_rate","recent_max_drawdown","backtest_paper_gap",
                "trade_count","overfit_score","action_required","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
