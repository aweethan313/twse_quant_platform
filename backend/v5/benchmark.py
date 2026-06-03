"""backend/v5/benchmark.py
0050 Buy-and-Hold Benchmark 計算。

V9.1-P0B 修正：
- 只使用 trading_calendar.is_open=1 的日級資料。
- 不新增分鐘資料，不要求 TPEx。
- 0050 於 2025-06-18 進行 1:4 分割；benchmark 用分割後同一價格基準計算。
- 排除 0050 分割停止交易期間 2025-06-11 ~ 2025-06-17 的誤寫入日 K。
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

CODE = "0050"
SPLIT_DATE = "2025-06-18"
SPLIT_RATIO = 4.0
SUSPEND_START = "2025-06-11"
SUSPEND_END = "2025-06-17"


def _ensure_benchmark_columns(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS benchmark_daily_equity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            benchmark_code TEXT NOT NULL DEFAULT '0050',
            snap_date TEXT NOT NULL,
            price REAL,
            shares REAL,
            equity REAL,
            daily_return REAL,
            cumulative_return REAL,
            initial_equity REAL DEFAULT 200000,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(benchmark_code, snap_date)
        )
    """))
    cols = {r[1] for r in db.execute(text("PRAGMA table_info(benchmark_daily_equity)")).fetchall()}
    if "is_valid" not in cols:
        db.execute(text("ALTER TABLE benchmark_daily_equity ADD COLUMN is_valid INTEGER DEFAULT 1"))
    if "anomaly_reason" not in cols:
        db.execute(text("ALTER TABLE benchmark_daily_equity ADD COLUMN anomaly_reason TEXT"))
    if "adjusted_price" not in cols:
        db.execute(text("ALTER TABLE benchmark_daily_equity ADD COLUMN adjusted_price REAL"))


def _adjusted_price(trade_date: str, raw_price: float | None) -> float | None:
    if raw_price is None:
        return None
    p = float(raw_price)
    if p <= 0:
        return None
    if str(trade_date) < SPLIT_DATE:
        return p / SPLIT_RATIO
    return p


def _get_price_rows(db, benchmark_code: str, start_date: str):
    rows = db.execute(text("""
        SELECT o.trade_date, o.open, o.close, o.volume
        FROM ohlcv_daily o
        LEFT JOIN trading_calendar tc ON tc.trade_date=o.trade_date
        WHERE o.code=:c
          AND o.trade_date>=:sd
          AND strftime('%w', o.trade_date) NOT IN ('0','6')
          AND COALESCE(tc.is_open, 1)=1
          AND NOT (o.trade_date BETWEEN :sus AND :sue)
          AND o.close IS NOT NULL AND o.close>0
        ORDER BY o.trade_date
    """), {"c": benchmark_code, "sd": start_date, "sus": SUSPEND_START, "sue": SUSPEND_END}).fetchall()
    return rows


def rebuild_0050_benchmark(
    start_date: str = "2026-05-25",
    initial_cash: float = 200000.0,
    benchmark_code: str = "0050",
) -> int:
    """重建 0050 Buy and Hold Benchmark。"""
    db = SessionLocal()
    try:
        _ensure_benchmark_columns(db)
        rows = _get_price_rows(db, benchmark_code, start_date)
        if not rows:
            logger.error(f"[BENCH] {benchmark_code} 無 {start_date} 後的有效交易日資料")
            return 0

        first_date, first_open, first_close, _ = rows[0]
        buy_price = _adjusted_price(str(first_date), first_open) or _adjusted_price(str(first_date), first_close)
        if not buy_price:
            logger.error(f"[BENCH] {benchmark_code} 第一筆買入價格無效")
            return 0

        shares = initial_cash / buy_price
        logger.info(
            f"[BENCH] {benchmark_code} buy_date={first_date} "
            f"adjusted_buy_price={buy_price:.4f} shares={shares:.2f}"
        )

        db.execute(text("DELETE FROM benchmark_daily_equity WHERE benchmark_code=:c AND snap_date>=:sd"),
                   {"c": benchmark_code, "sd": start_date})

        prev_equity = initial_cash
        updated = 0
        for trade_date, open_p, close_p, volume in rows:
            d = str(trade_date)
            price = _adjusted_price(d, close_p)
            if not price:
                continue
            equity = shares * price
            daily_ret = (equity / prev_equity - 1) * 100 if prev_equity > 0 else 0.0
            cum_ret = (equity / initial_cash - 1) * 100
            reason = None
            if abs(daily_ret) > 15:
                reason = f"large_adjusted_return={daily_ret:.2f}%"

            db.execute(text("""
                INSERT INTO benchmark_daily_equity
                    (benchmark_code, snap_date, price, shares, equity,
                     daily_return, cumulative_return, initial_equity,
                     is_valid, anomaly_reason, adjusted_price)
                VALUES (:c, :d, :p, :s, :e, :dr, :cr, :ie, 1, :ar, :ap)
                ON CONFLICT(benchmark_code, snap_date) DO UPDATE SET
                    price=excluded.price,
                    shares=excluded.shares,
                    equity=excluded.equity,
                    daily_return=excluded.daily_return,
                    cumulative_return=excluded.cumulative_return,
                    initial_equity=excluded.initial_equity,
                    is_valid=excluded.is_valid,
                    anomaly_reason=excluded.anomaly_reason,
                    adjusted_price=excluded.adjusted_price
            """), {
                "c": benchmark_code,
                "d": d,
                "p": round(price, 6),
                "s": shares,
                "e": round(equity, 2),
                "dr": round(daily_ret, 4),
                "cr": round(cum_ret, 4),
                "ie": initial_cash,
                "ar": reason,
                "ap": round(price, 6),
            })
            prev_equity = equity
            updated += 1

        db.commit()
        logger.success(f"[BENCH] {benchmark_code} benchmark 重建完成，{updated} 筆")
        return updated
    except Exception as e:
        db.rollback()
        logger.error(f"[BENCH] 失敗: {e}")
        return 0
    finally:
        db.close()


def get_benchmark_equity(start_date: str = None, end_date: str = None,
                         benchmark_code: str = "0050") -> list[dict]:
    db = SessionLocal()
    try:
        q = """
            SELECT snap_date, price, equity, daily_return, cumulative_return
            FROM benchmark_daily_equity
            WHERE benchmark_code=:c AND COALESCE(is_valid,1)=1
        """
        params = {"c": benchmark_code}
        if start_date:
            q += " AND snap_date>=:sd"; params["sd"] = start_date
        if end_date:
            q += " AND snap_date<=:ed"; params["ed"] = end_date
        q += " ORDER BY snap_date"
        rows = db.execute(text(q), params).fetchall()
        return [{"date": r[0], "price": r[1], "equity": r[2],
                 "daily_return": r[3], "cumulative_return": r[4]} for r in rows]
    finally:
        db.close()


def get_benchmark_comparison(account_equity_history: list[dict], start_date: str = None) -> dict:
    """計算策略 vs 0050 的 alpha。"""
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
            "alpha": round(float(strat_ret or 0) - float(bench_ret), 4),
        })
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
