"""
V4.6.1 market context diagnostics.

檢查：
- ohlcv_daily 每日 rows / common stocks
- stale common stocks 數量
- active universe 數量
- 修正後 active up/down/flat 與 avg return
- market_context_daily 目前儲存值
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


DB_PATH = Path("data/db/quant.db")


def _safe_float(x: Any, default: float | None = 0.0) -> float | None:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def _is_common_stock(code: str) -> bool:
    code = str(code)
    return len(code) == 4 and code.isdigit() and not code.startswith("00")


def _date_sub(date_str: str, days: int) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d - timedelta(days=days)).isoformat()


def _load_rows(conn: sqlite3.Connection, start: str, end: str) -> dict[str, list[dict[str, Any]]]:
    hist_start = _date_sub(start, 90)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT code, trade_date, open, high, low, close, volume, value, change_pct
        FROM ohlcv_daily
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY code, trade_date
    """, (hist_start, end)).fetchall()

    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_code[str(r["code"])].append({
            "code": str(r["code"]),
            "trade_date": str(r["trade_date"]),
            "open": _safe_float(r["open"], None),
            "high": _safe_float(r["high"], None),
            "low": _safe_float(r["low"], None),
            "close": _safe_float(r["close"], None),
            "volume": _safe_float(r["volume"], 0.0),
            "value": _safe_float(r["value"], 0.0),
            "stored_change_pct": _safe_float(r["change_pct"], None),
        })
    return by_code


def _features_by_date(by_code: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for code, rows in by_code.items():
        prev_close = None
        prev_key = None
        stale_streak = 1

        for item in rows:
            key = (item["open"], item["high"], item["low"], item["close"], item["volume"])
            if prev_key is not None and key == prev_key:
                stale_streak += 1
            else:
                stale_streak = 1

            close = item["close"]
            if prev_close and prev_close > 0 and close and close > 0:
                actual = (close / prev_close - 1) * 100
            else:
                actual = item["stored_change_pct"] if item["stored_change_pct"] is not None else 0.0

            f = dict(item)
            f["is_common_stock"] = _is_common_stock(code)
            f["stale_streak"] = stale_streak
            f["actual_change_pct"] = actual
            by_date[item["trade_date"]].append(f)

            prev_close = close
            prev_key = key

    return by_date


def _is_active(r: dict[str, Any], stale_days: int) -> bool:
    if not r["is_common_stock"]:
        return False
    if r["close"] is None or r["close"] <= 0:
        return False
    if r["open"] is None or r["high"] is None or r["low"] is None:
        return False
    if (r["volume"] or 0) <= 0:
        return False
    if r["stale_streak"] >= stale_days:
        return False
    if abs(float(r["actual_change_pct"])) > 15:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--stale-days", type=int, default=5)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    by_code = _load_rows(conn, args.start_date, args.end_date)
    by_date = _features_by_date(by_code)

    print("=" * 128)
    print("V4.6.1 market context diagnostics")
    print(f"range: {args.start_date} ~ {args.end_date}")
    print(f"db: {args.db_path}")
    print("=" * 128)
    print("date        rows common stale active   up down flat  up_ratio  avg_ret  stored_bias stored_regime")
    print("-" * 128)

    dates = [r[0] for r in conn.execute("""
        SELECT DISTINCT trade_date FROM ohlcv_daily
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, (args.start_date, args.end_date)).fetchall()]

    for d in dates:
        rows = by_date.get(str(d), [])
        common = [r for r in rows if r["is_common_stock"]]
        stale = [r for r in common if r["stale_streak"] >= args.stale_days]
        active = [r for r in rows if _is_active(r, args.stale_days)]
        changes = [float(r["actual_change_pct"]) for r in active]
        up = sum(1 for x in changes if x > 0.001)
        down = sum(1 for x in changes if x < -0.001)
        flat = len(changes) - up - down
        up_ratio = up / len(changes) * 100 if changes else 0.0
        avg_ret = sum(changes) / len(changes) if changes else 0.0

        m = conn.execute("""
            SELECT market_bias_score, trend_regime
            FROM market_context_daily
            WHERE context_date = ?
        """, (str(d),)).fetchone()
        if m:
            stored_bias = f"{(m[0] or 0):6.1f}"
            stored_regime = str(m[1])
        else:
            stored_bias = "   n/a"
            stored_regime = "n/a"

        print(
            f"{str(d):10s} {len(rows):4d} {len(common):6d} {len(stale):5d} {len(active):6d} "
            f"{up:4d} {down:4d} {flat:4d} {up_ratio:8.2f}% {avg_ret:+8.3f}% "
            f"{stored_bias:>11s} {stored_regime}"
        )

    conn.close()


if __name__ == "__main__":
    main()
