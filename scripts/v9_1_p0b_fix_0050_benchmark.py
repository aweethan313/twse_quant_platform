#!/usr/bin/env python3
"""V9.1-P0B 0050 benchmark repair.

範圍：
- 不新增分鐘資料表
- 不要求 TPEx
- 只修 0050 benchmark / 0050 分割期間資料污染

修正內容：
1. 0050 在 2025-06-11 ~ 2025-06-17 期間因 1:4 分割停止交易，刪除 DB 中誤寫入的 0050 日 K 與下游分數。
2. 重建 benchmark_daily_equity：2025-06-18 前的 0050 價格除以 4，轉成分割後同一價格基準。
3. 產生 data/reports/v9_1_p0b_0050_benchmark_report.md。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "db" / "quant.db"
REPORT_DIR = ROOT / "data" / "reports"
INIT_CASH = 200_000.0
CODE = "0050"
START_DATE = "2025-01-01"
SPLIT_DATE = "2025-06-18"
SPLIT_RATIO = 4.0
SUSPEND_START = "2025-06-11"
SUSPEND_END = "2025-06-17"
MAX_WARN_ABS_RET = 15.0

DELETE_CODE_DATE_TABLES = [
    ("ohlcv_daily", "code", "trade_date"),
    ("technical_daily_features", "code", "trade_date"),
    ("daily_scores", "code", "score_date"),
    ("ml_score_results", "code", "score_date"),
    ("chip_daily", "code", "trade_date"),
]


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    if not table_exists(cur, table):
        return False
    return column in {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_benchmark(cur: sqlite3.Cursor) -> None:
    cur.execute("""
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
    """)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(benchmark_daily_equity)").fetchall()}
    if "is_valid" not in cols:
        cur.execute("ALTER TABLE benchmark_daily_equity ADD COLUMN is_valid INTEGER DEFAULT 1")
    if "anomaly_reason" not in cols:
        cur.execute("ALTER TABLE benchmark_daily_equity ADD COLUMN anomaly_reason TEXT")
    if "adjusted_price" not in cols:
        cur.execute("ALTER TABLE benchmark_daily_equity ADD COLUMN adjusted_price REAL")


def adjusted_price(trade_date: str, raw_price: float | None) -> float | None:
    if raw_price is None:
        return None
    p = float(raw_price)
    if p <= 0:
        return None
    if trade_date < SPLIT_DATE:
        return p / SPLIT_RATIO
    return p


def clean_0050_suspended_rows(cur: sqlite3.Cursor) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table, code_col, date_col in DELETE_CODE_DATE_TABLES:
        if not (table_exists(cur, table) and column_exists(cur, table, code_col) and column_exists(cur, table, date_col)):
            continue
        n = cur.execute(
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE {code_col}=? AND {date_col} BETWEEN ? AND ?
            """,
            (CODE, SUSPEND_START, SUSPEND_END),
        ).fetchone()[0]
        if n:
            cur.execute(
                f"""
                DELETE FROM {table}
                WHERE {code_col}=? AND {date_col} BETWEEN ? AND ?
                """,
                (CODE, SUSPEND_START, SUSPEND_END),
            )
        deleted[f"{table}.{date_col}"] = int(n)
    return deleted


def load_0050_rows(cur: sqlite3.Cursor):
    return cur.execute(
        """
        SELECT o.trade_date, o.open, o.close, o.volume
        FROM ohlcv_daily o
        LEFT JOIN trading_calendar tc ON tc.trade_date=o.trade_date
        WHERE o.code=?
          AND o.trade_date>=?
          AND COALESCE(tc.is_open, 1)=1
          AND strftime('%w', o.trade_date) NOT IN ('0','6')
          AND NOT (o.trade_date BETWEEN ? AND ?)
          AND o.close IS NOT NULL AND o.close>0
        ORDER BY o.trade_date
        """,
        (CODE, START_DATE, SUSPEND_START, SUSPEND_END),
    ).fetchall()


def rebuild_benchmark(cur: sqlite3.Cursor) -> dict:
    ensure_benchmark(cur)
    rows = load_0050_rows(cur)
    if not rows:
        return {"updated": 0, "start": None, "end": None, "total_return": None, "max_abs_daily_return": None}

    first_date, first_open, first_close, _ = rows[0]
    buy_price = adjusted_price(str(first_date), first_open) or adjusted_price(str(first_date), first_close)
    if not buy_price:
        raise RuntimeError("0050 first adjusted buy price is invalid")

    shares = INIT_CASH / buy_price
    cur.execute("DELETE FROM benchmark_daily_equity WHERE benchmark_code=? AND snap_date>=?", (CODE, START_DATE))

    prev_equity = INIT_CASH
    updated = 0
    last_equity = INIT_CASH
    max_abs_daily_return = 0.0
    anomalies: list[str] = []

    for trade_date, open_p, close_p, volume in rows:
        d = str(trade_date)
        adj_close = adjusted_price(d, close_p)
        if not adj_close:
            anomalies.append(f"{d}: invalid close={close_p}")
            continue
        equity = shares * adj_close
        daily_return = (equity / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0
        cumulative_return = (equity / INIT_CASH - 1.0) * 100.0
        max_abs_daily_return = max(max_abs_daily_return, abs(daily_return))
        reason = None
        is_valid = 1
        if abs(daily_return) > MAX_WARN_ABS_RET:
            # 不硬刪，避免誤傷真實極端行情；但留下明確標記。
            reason = f"large_adjusted_return={daily_return:.2f}%"
            anomalies.append(f"{d}: adjusted_return={daily_return:.2f}% raw_close={close_p} adj_close={adj_close:.4f}")

        cur.execute(
            """
            INSERT INTO benchmark_daily_equity
                (benchmark_code, snap_date, price, shares, equity, daily_return,
                 cumulative_return, initial_equity, is_valid, anomaly_reason, adjusted_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            """,
            (
                CODE, d, round(adj_close, 6), shares, round(equity, 2),
                round(daily_return, 4), round(cumulative_return, 4), INIT_CASH,
                is_valid, reason, round(adj_close, 6),
            ),
        )
        prev_equity = equity
        last_equity = equity
        updated += 1

    return {
        "updated": updated,
        "start": str(rows[0][0]) if rows else None,
        "end": str(rows[-1][0]) if rows else None,
        "buy_price_adjusted": round(float(buy_price), 6),
        "shares": round(float(shares), 6),
        "total_return": round((last_equity / INIT_CASH - 1.0) * 100.0, 4),
        "max_abs_daily_return": round(max_abs_daily_return, 4),
        "anomalies": anomalies[:20],
    }


def top_benchmark_returns(cur: sqlite3.Cursor, limit: int = 15):
    return cur.execute(
        """
        SELECT snap_date, price, ROUND(daily_return, 4), ROUND(cumulative_return, 4), anomaly_reason
        FROM benchmark_daily_equity
        WHERE benchmark_code=? AND snap_date>=?
        ORDER BY ABS(daily_return) DESC
        LIMIT ?
        """,
        (CODE, START_DATE, limit),
    ).fetchall()


def write_report(deleted: dict[str, int], bench: dict, top_rows) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "v9_1_p0b_0050_benchmark_report.md"
    lines = [
        "# V9.1-P0B 0050 Benchmark Repair Report",
        "",
        f"- generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- scope: TWSE 上市股票 + ETF、日級資料；沒有加入分鐘資料表，沒有要求 TPEx",
        f"- 0050 split: {SPLIT_DATE} 前價格除以 {SPLIT_RATIO:g}，轉成分割後同一價格基準",
        f"- 0050 suspended period removed: {SUSPEND_START} ~ {SUSPEND_END}",
        "",
        "## Deleted 0050 Suspended Rows",
    ]
    for k, v in deleted.items():
        lines.append(f"- {k}: {v}")
    lines += [
        "",
        "## Rebuilt Benchmark",
    ]
    for k in ["updated", "start", "end", "buy_price_adjusted", "shares", "total_return", "max_abs_daily_return"]:
        lines.append(f"- {k}: {bench.get(k)}")
    lines += ["", "## Top Benchmark Daily Returns"]
    for r in top_rows:
        lines.append(f"- {r[0]} | price={r[1]} | daily_return={r[2]}% | cumulative={r[3]}% | reason={r[4]}")
    if bench.get("anomalies"):
        lines += ["", "## Warnings"]
        for a in bench["anomalies"]:
            lines.append(f"- {a}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    backup = DB_PATH.with_name(f"quant_backup_before_v9_1_p0b_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    if args.apply:
        shutil.copy2(DB_PATH, backup)

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        deleted = clean_0050_suspended_rows(cur)
        bench = rebuild_benchmark(cur)
        top_rows = top_benchmark_returns(cur)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        report = write_report(deleted, bench, top_rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("=== V9.1-P0B 0050 benchmark repair completed ===")
    print(f"apply: {args.apply}")
    print(f"backup: {backup if args.apply else '(dry-run, no backup written)'}")
    print(f"benchmark: {bench}")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
