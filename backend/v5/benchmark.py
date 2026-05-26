"""backend/v5/benchmark.py
0050 Buy and Hold Benchmark 計算
"""
from __future__ import annotations
from datetime import date, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def rebuild_0050_benchmark(
    start_date: str = "2025-01-01",
    initial_cash: float = 200000.0,
    benchmark_code: str = "0050",
) -> int:
    """
    重建 0050 Buy and Hold Benchmark
    從 start_date 開始，用 initial_cash 買入 0050，每天計算市值
    """
    db = SessionLocal()
    try:
        # 取 start_date 的開盤價買入
        first = db.execute(text("""
            SELECT trade_date, open, close FROM ohlcv_daily
            WHERE code=:c AND trade_date>=:sd
            ORDER BY trade_date LIMIT 1
        """), {"c": benchmark_code, "sd": start_date}).fetchone()

        if not first:
            logger.error(f"[BENCH] {benchmark_code} 無 {start_date} 後的資料")
            return 0

        buy_date = first[0]
        buy_price = float(first[1] or first[2])  # open 優先，fallback close
        shares = initial_cash / buy_price

        logger.info(f"[BENCH] {benchmark_code} 買入日={buy_date} 價格={buy_price:.2f} 股數={shares:.2f}")

        # 刪除舊資料
        db.execute(text(
            "DELETE FROM benchmark_daily_equity WHERE benchmark_code=:c AND snap_date>=:sd"
        ), {"c": benchmark_code, "sd": start_date})

        # 取所有交易日
        rows = db.execute(text("""
            SELECT trade_date, close FROM ohlcv_daily
            WHERE code=:c AND trade_date>=:sd
            ORDER BY trade_date
        """), {"c": benchmark_code, "sd": buy_date}).fetchall()

        prev_equity = initial_cash
        updated = 0
        for trade_date, close in rows:
            close_f = float(close or buy_price)
            equity = shares * close_f
            daily_ret = (equity / prev_equity - 1) * 100 if prev_equity > 0 else 0
            cum_ret = (equity / initial_cash - 1) * 100

            db.execute(text("""
                INSERT INTO benchmark_daily_equity
                    (benchmark_code, snap_date, price, shares, equity,
                     daily_return, cumulative_return, initial_equity)
                VALUES (:c, :d, :p, :s, :e, :dr, :cr, :ie)
                ON CONFLICT(benchmark_code, snap_date) DO UPDATE SET
                    price=excluded.price, equity=excluded.equity,
                    daily_return=excluded.daily_return,
                    cumulative_return=excluded.cumulative_return
            """), {
                "c": benchmark_code, "d": trade_date,
                "p": close_f, "s": shares, "e": equity,
                "dr": round(daily_ret, 4),
                "cr": round(cum_ret, 4),
                "ie": initial_cash,
            })

            prev_equity = equity
            updated += 1

        db.commit()
        logger.success(f"[BENCH] {benchmark_code} benchmark 建立完成，{updated} 筆")
        return updated

    except Exception as e:
        db.rollback()
        logger.error(f"[BENCH] 失敗: {e}")
        return 0
    finally:
        db.close()


def get_benchmark_equity(
    start_date: str = None,
    end_date: str = None,
    benchmark_code: str = "0050",
) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT snap_date, price, equity, daily_return, cumulative_return FROM benchmark_daily_equity WHERE benchmark_code=:c"
        params = {"c": benchmark_code}
        if start_date: q += " AND snap_date>=:sd"; params["sd"] = start_date
        if end_date:   q += " AND snap_date<=:ed"; params["ed"] = end_date
        q += " ORDER BY snap_date"
        rows = db.execute(text(q), params).fetchall()
        return [{"date": r[0], "price": r[1], "equity": r[2],
                 "daily_return": r[3], "cumulative_return": r[4]} for r in rows]
    finally:
        db.close()


def get_benchmark_comparison(
    account_equity_history: list[dict],
    start_date: str = None,
) -> dict:
    """計算策略 vs 0050 的 alpha"""
    db = SessionLocal()
    try:
        if not account_equity_history:
            return {}

        sd = start_date or account_equity_history[0].get("date", "2025-01-01")

        bench = get_benchmark_equity(start_date=sd)
        bench_map = {r["date"]: r["cumulative_return"] for r in bench}

        results = []
        for row in account_equity_history:
            d = row.get("date") or row.get("snap_date")
            strat_ret = row.get("cumulative_return") or 0
            bench_ret = bench_map.get(str(d), 0)
            results.append({
                "date": d,
                "strategy_return": strat_ret,
                "benchmark_return": bench_ret,
                "alpha": round(float(strat_ret or 0) - float(bench_ret or 0), 4),
            })

        # 最新 alpha
        latest_alpha = results[-1]["alpha"] if results else 0
        latest_bench = results[-1]["benchmark_return"] if results else 0
        latest_strat = results[-1]["strategy_return"] if results else 0

        return {
            "start_date": sd,
            "latest_strategy_return": latest_strat,
            "latest_benchmark_return": latest_bench,
            "alpha": latest_alpha,
            "outperform": latest_alpha > 0,
            "history": results,
        }
    finally:
        db.close()
