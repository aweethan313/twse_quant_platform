"""
Diagnose stale OHLCV rows.

Stale = consecutive rows for the same code whose (open, high, low, close, volume) are identical.
This often indicates a bad backfill/import process, not real market behavior.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


DB_PATH = Path("data/db/quant.db")
REPORT_DIR = Path("data/reports")


def _safe_float(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2026-04-16")
    parser.add_argument("--end-date", default="2026-05-18")
    parser.add_argument("--min-stale-days", type=int, default=5)
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--db-path", default=str(DB_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()

    cur.execute("""
    SELECT code, trade_date, open, high, low, close, volume, change_pct
    FROM ohlcv_daily
    WHERE trade_date BETWEEN ? AND ?
    ORDER BY code, trade_date
    """, (args.start_date, args.end_date))

    by_code: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for row in cur.fetchall():
        code, d, o, h, l, c, v, cp = row
        by_code[str(code)].append((str(d), _safe_float(o), _safe_float(h), _safe_float(l), _safe_float(c), _safe_float(v), _safe_float(cp)))

    suspicious = []

    for code, rows in by_code.items():
        if len(rows) < args.min_stale_days:
            continue

        longest = 1
        current = 1
        longest_start = rows[0][0]
        current_start = rows[0][0]
        longest_end = rows[0][0]
        prev_key = None

        for row in rows:
            d, o, h, l, c, v, cp = row
            key = (o, h, l, c, v)

            if key == prev_key:
                current += 1
            else:
                current = 1
                current_start = d

            if current > longest:
                longest = current
                longest_start = current_start
                longest_end = d

            prev_key = key

        if longest >= args.min_stale_days:
            suspicious.append({
                "code": code,
                "longest_stale": longest,
                "rows": len(rows),
                "stale_start": longest_start,
                "stale_end": longest_end,
            })

    suspicious.sort(key=lambda x: (x["longest_stale"], x["rows"]), reverse=True)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"stale_ohlcv_{args.start_date}_{args.end_date}.csv"
    with report_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "longest_stale", "rows", "stale_start", "stale_end"])
        writer.writeheader()
        writer.writerows(suspicious)

    print("疑似重複 OHLCV 股票")
    print("=" * 80)
    print(f"期間：{args.start_date} ~ {args.end_date}")
    print(f"連續完全相同 OHLCV >= {args.min_stale_days} 天")
    print(f"可疑股票數：{len(suspicious)}")
    print(f"報告：{report_path}")
    print("=" * 80)

    for item in suspicious[: args.limit]:
        print(
            f"{item['code']:>8}  longest_stale={item['longest_stale']:2d}  "
            f"rows={item['rows']:2d}  {item['stale_start']}~{item['stale_end']}"
        )

    conn.close()


if __name__ == "__main__":
    main()
