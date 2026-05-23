"""
backend/v3/walk_forward_validator.py
V3-FIX-5：Walk-forward 滾動驗證
- Train 6個月 → Test 1個月
- 每次往後滾動1個月
- 偵測過擬合
"""
from __future__ import annotations
import json
import math
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def _months_between(d1: date, d2: date) -> int:
    return (d2.year - d1.year) * 12 + d2.month - d1.month


def _get_equity_returns(strategy_id: int, start: date, end: date, db) -> list[float]:
    """取區間內的月報酬率序列"""
    rows = db.execute(text("""
        SELECT snap_date, total_equity FROM equity_curve
        WHERE account_id=:sid AND snap_date BETWEEN :s AND :e
        ORDER BY snap_date
    """), {"sid": strategy_id, "s": str(start), "e": str(end)}).fetchall()

    if len(rows) < 2:
        return []

    totals = [float(r[1]) for r in rows]
    # 月末報酬率
    monthly = {}
    for r in rows:
        m = str(r[0])[:7]
        monthly[m] = float(r[1])

    months = sorted(monthly.keys())
    rets = []
    for i in range(1, len(months)):
        prev = monthly[months[i-1]]
        curr = monthly[months[i]]
        if prev > 0:
            rets.append((curr / prev - 1) * 100)
    return rets


def _compute_metrics(returns: list[float], trades_count: int = 0) -> dict:
    """計算績效指標"""
    if not returns:
        return {"total_return": 0, "max_drawdown": 0, "win_rate": 0,
                "profit_factor": 1, "stability": 0, "trade_count": trades_count}

    # 累積報酬
    total = 1.0
    for r in returns:
        total *= (1 + r / 100)
    total_return = (total - 1) * 100

    # Max drawdown（月度）
    peak = 1.0; curr = 1.0; mdd = 0.0
    for r in returns:
        curr *= (1 + r / 100)
        if curr > peak: peak = curr
        dd = (peak - curr) / peak * 100
        if dd > mdd: mdd = dd

    # Win rate（月報酬 > 0 的比例）
    wins = sum(1 for r in returns if r > 0)
    wr = wins / len(returns) * 100 if returns else 0

    # Profit factor
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss   = abs(sum(r for r in returns if r < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else (2.0 if gross_profit > 0 else 1.0)

    # Stability（標準差越小越穩定）
    if len(returns) >= 2:
        avg = sum(returns) / len(returns)
        std = math.sqrt(sum((r-avg)**2 for r in returns) / len(returns))
        stab = max(0, 100 - std * 5)
    else:
        stab = 50

    return {
        "total_return": round(total_return, 2),
        "max_drawdown": round(mdd, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "stability": round(stab, 1),
        "trade_count": trades_count,
    }


def _detect_overfit(train: dict, test: dict) -> tuple[float, list[str]]:
    """偵測過擬合，回傳 (overfit_score, warnings)"""
    warnings = []
    score = 0.0

    # Train 賺很多但 Test 很差
    if train["total_return"] > 10 and test["total_return"] < -5:
        score += 30
        warnings.append(f"Train+{train['total_return']:.1f}% 但 Test{test['total_return']:.1f}%")

    # 回撤過大
    if test["max_drawdown"] > 20:
        score += 20
        warnings.append(f"Test 最大回撤 {test['max_drawdown']:.1f}%")

    # 交易太少
    if test["trade_count"] < 3:
        score += 15
        warnings.append(f"Test 交易次數僅 {test['trade_count']} 筆")

    # Profit factor 異常
    if train["profit_factor"] > 4 and test["profit_factor"] < 1:
        score += 25
        warnings.append(f"Train PF={train['profit_factor']:.1f} 但 Test PF={test['profit_factor']:.1f}")

    # 勝率高但盈虧比差
    if test["win_rate"] > 70 and test["profit_factor"] < 1.2:
        score += 10
        warnings.append("勝率高但盈虧比差，可能過度擬合")

    # 穩定性差
    if test["stability"] < 30:
        score += 10
        warnings.append(f"Test 月度波動過大（穩定分 {test['stability']:.0f}）")

    return round(min(100, score), 1), warnings


def run_walk_forward(
    strategy_id: int,
    data_start: date,
    data_end: date,
    train_months: int = 6,
    test_months: int = 1,
    parameter_set: dict = None,
) -> list[dict]:
    """
    執行 Walk-forward 驗證
    回傳每段 train/test 的結果
    """
    db = SessionLocal()
    results = []

    try:
        cursor = data_start
        segment = 0

        while True:
            train_start = cursor
            train_end   = cursor + relativedelta(months=train_months) - relativedelta(days=1)
            test_start  = train_end + relativedelta(days=1)
            test_end    = test_start + relativedelta(months=test_months) - relativedelta(days=1)

            if test_end > data_end:
                break

            segment += 1
            logger.info(f"[WF] S{strategy_id} 段{segment}: "
                        f"Train {train_start}~{train_end} | Test {test_start}~{test_end}")

            # 取報酬序列
            train_rets = _get_equity_returns(strategy_id, train_start, train_end, db)
            test_rets  = _get_equity_returns(strategy_id, test_start, test_end, db)

            # 取交易次數
            train_trades = db.execute(text("""
                SELECT COUNT(*) FROM trade_logs
                WHERE account_id=:sid AND trade_date BETWEEN :s AND :e
                  AND direction='sell'
            """), {"sid": strategy_id, "s": str(train_start), "e": str(train_end)}).scalar() or 0
            test_trades = db.execute(text("""
                SELECT COUNT(*) FROM trade_logs
                WHERE account_id=:sid AND trade_date BETWEEN :s AND :e
                  AND direction='sell'
            """), {"sid": strategy_id, "s": str(test_start), "e": str(test_end)}).scalar() or 0

            train_m = _compute_metrics(train_rets, int(train_trades))
            test_m  = _compute_metrics(test_rets,  int(test_trades))

            overfit_score, warnings = _detect_overfit(train_m, test_m)

            row = {
                "strategy_id": strategy_id,
                "parameter_set": json.dumps(parameter_set or {}),
                "train_start": str(train_start), "train_end": str(train_end),
                "test_start":  str(test_start),  "test_end":  str(test_end),
                "train_return":    train_m["total_return"],
                "test_return":     test_m["total_return"],
                "test_max_drawdown": test_m["max_drawdown"],
                "test_win_rate":     test_m["win_rate"],
                "test_profit_factor": test_m["profit_factor"],
                "test_trade_count":   test_m["trade_count"],
                "stability_score":    test_m["stability"],
                "overfit_score":      overfit_score,
                "overfit_warning":    "；".join(warnings) if warnings else None,
                "segment": segment,
            }

            # 寫入 DB
            db.execute(text("""
                INSERT INTO walk_forward_results (
                    strategy_id, parameter_set_json,
                    train_start, train_end, test_start, test_end,
                    train_return, test_return, test_max_drawdown,
                    test_win_rate, test_profit_factor, test_trade_count,
                    stability_score, overfit_score, overfit_warning
                ) VALUES (
                    :sid, :ps, :trs, :tre, :tes, :tee,
                    :trr, :ter, :mdd, :wr, :pf, :tc, :ss, :os, :ow
                )
            """), {
                "sid": strategy_id, "ps": row["parameter_set"],
                "trs": row["train_start"], "tre": row["train_end"],
                "tes": row["test_start"],  "tee": row["test_end"],
                "trr": row["train_return"], "ter": row["test_return"],
                "mdd": row["test_max_drawdown"], "wr": row["test_win_rate"],
                "pf": row["test_profit_factor"], "tc": row["test_trade_count"],
                "ss": row["stability_score"], "os": row["overfit_score"],
                "ow": row["overfit_warning"],
            })
            results.append(row)
            cursor += relativedelta(months=test_months)

        db.commit()

        # 摘要統計
        if results:
            avg_test = sum(r["test_return"] for r in results) / len(results)
            avg_train = sum(r["train_return"] for r in results) / len(results)
            has_overfit = any(r["overfit_score"] > 30 for r in results)
            logger.info(f"[WF] S{strategy_id} 完成 {len(results)} 段 "
                        f"avg_train={avg_train:.1f}% avg_test={avg_test:.1f}% "
                        f"overfit={'⚠️' if has_overfit else 'OK'}")

        return results
    except Exception as e:
        logger.error(f"[WF] S{strategy_id} 失敗: {e}")
        db.rollback()
        return []
    finally:
        db.close()


def run_all_strategies_walk_forward(data_start: date = None, data_end: date = None):
    """對所有策略執行 Walk-forward"""
    db = SessionLocal()
    try:
        strategies = db.execute(text("SELECT id, name FROM strategy_accounts ORDER BY id")).fetchall()
        if not data_end:   data_end   = date.today()
        if not data_start: data_start = date(2025, 2, 1)

        all_results = {}
        for sid, sname in strategies:
            logger.info(f"[WF] 執行 S{sid} {sname}...")
            results = run_walk_forward(sid, data_start, data_end)
            all_results[sid] = {"name": sname, "segments": results}

        return all_results
    finally:
        db.close()


def get_walk_forward_results(strategy_id: int = None, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM walk_forward_results WHERE 1=1"
        params = {}
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        q += " ORDER BY strategy_id, train_start LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","strategy_id","parameter_set_json","train_start","train_end",
                "test_start","test_end","train_return","test_return","test_max_drawdown",
                "test_win_rate","test_profit_factor","test_trade_count",
                "stability_score","overfit_score","overfit_warning","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
