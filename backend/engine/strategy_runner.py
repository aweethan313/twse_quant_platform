"""
backend/engine/strategy_runner.py
執行所有啟用策略帳戶。

重點：run_all_strategies(trade_date) 的 trade_date 是「實際交易日」。
策略內部會自動使用 trade_date 之前最近一個 score_date，避免用當日收盤分數買當日收盤價。
"""
from datetime import date
from loguru import logger
from sqlalchemy import text

from backend.models.database import SessionLocal, StrategyAccount
from backend.strategies.base_strategy import build_strategy


def _to_date(x):
    if isinstance(x, date):
        return x
    if x is None:
        return None
    return date.fromisoformat(str(x)[:10])


def _build_price_map(db, trade_date: date) -> dict[str, float]:
    rows = db.execute(
        text("SELECT code, close FROM ohlcv_daily WHERE trade_date=:d"),
        {"d": trade_date},
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows if r[1] is not None and float(r[1]) > 0}


def _latest_trade_date(db) -> date | None:
    row = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).fetchone()
    return _to_date(row[0]) if row and row[0] else None


def run_all_strategies(trade_date: date = None):
    db = SessionLocal()
    try:
        trade_date = trade_date or _latest_trade_date(db) or date.today()
        price_map = _build_price_map(db, trade_date)
        if not price_map:
            logger.warning(f"[RUNNER] {trade_date} 無行情資料，策略略過")
            return

        accounts = db.query(StrategyAccount).filter_by(is_active=True).all()
        logger.info(f"[RUNNER] {trade_date} 執行 {len(accounts)} 個策略帳戶")

        for acc in accounts:
            try:
                strategy = build_strategy(
                    account_id=acc.id,
                    class_name=acc.strategy_class or "MomentumBreakout",
                    params=acc.params or {},
                )
                strategy.run(trade_date, price_map)
                logger.info(f"[RUNNER] {acc.name} 執行完成")
            except Exception as e:
                logger.error(f"[RUNNER] {acc.name} 失敗: {e}")
    finally:
        db.close()


def snapshot_all_equity(trade_date: date = None):
    db = SessionLocal()
    try:
        trade_date = trade_date or _latest_trade_date(db) or date.today()
        price_map = _build_price_map(db, trade_date)
        accounts = db.query(StrategyAccount).filter_by(is_active=True).all()
        from backend.engine.paper_account import PaperAccount
        for acc in accounts:
            PaperAccount(acc.id).snapshot_equity(price_map, trade_date)
    finally:
        db.close()


def get_competition_ranking(start_date: date, end_date: date = None) -> list[dict]:
    end_date = end_date or date.today()
    db = SessionLocal()
    try:
        accounts = db.query(StrategyAccount).all()
        result = []
        for acc in accounts:
            start_row = db.execute(
                text("""SELECT total_equity FROM equity_curve
                        WHERE account_id=:id AND snap_date>=:s
                        ORDER BY snap_date ASC LIMIT 1"""),
                {"id": acc.id, "s": start_date},
            ).fetchone()
            end_row = db.execute(
                text("""SELECT total_equity, snap_date FROM equity_curve
                        WHERE account_id=:id AND snap_date<=:e
                        ORDER BY snap_date DESC LIMIT 1"""),
                {"id": acc.id, "e": end_date},
            ).fetchone()
            if not end_row:
                continue

            start_eq = float(start_row[0]) if start_row else float(acc.initial_cash or 0)
            end_eq = float(end_row[0])
            ret_pct = (end_eq - start_eq) / start_eq * 100 if start_eq else 0

            curve_rows = db.execute(
                text("""SELECT total_equity FROM equity_curve
                        WHERE account_id=:id AND snap_date BETWEEN :s AND :e
                        ORDER BY snap_date"""),
                {"id": acc.id, "s": start_date, "e": end_date},
            ).fetchall()
            equities = [float(r[0]) for r in curve_rows if r[0] is not None]
            max_dd = 0.0
            if equities:
                peak = equities[0]
                for eq in equities:
                    peak = max(peak, eq)
                    dd = (eq - peak) / peak if peak else 0
                    max_dd = min(max_dd, dd)

            result.append({
                "account_id": acc.id,
                "name": acc.name,
                "strategy_class": acc.strategy_class,
                "start_equity": round(start_eq, 2),
                "end_equity": round(end_eq, 2),
                "return_pct": round(ret_pct, 4),
                "max_drawdown": round(max_dd * 100, 4),
                "latest_date": str(end_row[1]),
            })

        result.sort(key=lambda x: x["return_pct"], reverse=True)
        for i, r in enumerate(result):
            r["rank"] = i + 1
        return result
    finally:
        db.close()
