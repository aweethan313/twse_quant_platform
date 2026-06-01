#!/usr/bin/env python3
"""V9.1-P0 daily-only fix.

修正範圍：
1. 建立/修正 trading_calendar，週末與已知 TWSE 休市日標成 is_open=0。
2. 從日級核心表刪除非交易日污染列。
3. 重建 0050 benchmark，並用連續價格鏈處理分割/壞價尺度跳動。
4. 產生 data/reports/v9_1_p0_fix_report.md。

注意：此腳本不建立分鐘資料表、不補 TPEx；目前範圍是 TWSE 上市股票 + ETF、日級資料。
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "db" / "quant.db"
REPORT_DIR = ROOT / "data" / "reports"
INIT_CASH = 200_000.0
BENCHMARK_CODE = "0050"
MAX_ABS_DAILY_RETURN = 0.15

# 已知 TWSE 休市日。這不是要做完整萬年曆，而是避免目前 DB 中已觀察到的假日資料污染。
# 之後若你要補更完整年份，可以只擴充這個 set 或改接官方交易日曆 CSV。
KNOWN_TWSE_CLOSED_DATES = {
    # 2025
    "2025-01-01",
    "2025-01-23", "2025-01-24", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-28",
    "2025-04-03", "2025-04-04",
    "2025-05-01",
    "2025-05-30",
    "2025-10-06",
    "2025-10-10",
    # 2026（目前資料到 2026-06-01，先放上半年與常見休市日）
    "2026-01-01",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-27",
    "2026-04-03", "2026-04-06",
    "2026-05-01",
}

DATE_TABLES = [
    ("ohlcv_daily", "trade_date"),
    ("technical_daily_features", "trade_date"),
    ("daily_scores", "score_date"),
    ("chip_daily", "trade_date"),
    ("ml_score_results", "score_date"),
    ("benchmark_daily_equity", "snap_date"),
    ("factor_store", "trade_date"),
    ("candidate_forward_returns", "score_date"),
    ("data_quality_checks", "check_date"),
]


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    if not table_exists(cur, table):
        return False
    return column in {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_trading_calendar(cur: sqlite3.Cursor):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_open INTEGER DEFAULT 1,
            weekday INTEGER,
            source TEXT DEFAULT 'v9_1_p0_fix',
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)


def collect_dates(cur: sqlite3.Cursor) -> list[str]:
    dates = set()
    for table, col in DATE_TABLES:
        if table_exists(cur, table) and column_exists(cur, table, col):
            for (d,) in cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL"):
                dates.add(str(d)[:10])
    if table_exists(cur, "trading_calendar"):
        for (d,) in cur.execute("SELECT DISTINCT trade_date FROM trading_calendar WHERE trade_date IS NOT NULL"):
            dates.add(str(d)[:10])
    return sorted(dates)


def rebuild_trading_calendar(cur: sqlite3.Cursor, dates: list[str]) -> set[str]:
    closed = set()
    for d in dates:
        try:
            wd = date.fromisoformat(d).weekday()
        except ValueError:
            continue
        is_weekend = wd >= 5
        is_known_closed = d in KNOWN_TWSE_CLOSED_DATES
        is_open = 0 if (is_weekend or is_known_closed) else 1
        note = "weekend" if is_weekend else ("known_twse_closed" if is_known_closed else "")
        if not is_open:
            closed.add(d)
        cur.execute("""
            INSERT INTO trading_calendar (trade_date, is_open, weekday, source, note, updated_at)
            VALUES (?, ?, ?, 'v9_1_p0_fix', ?, datetime('now','localtime'))
            ON CONFLICT(trade_date) DO UPDATE SET
                is_open=excluded.is_open,
                weekday=excluded.weekday,
                source=excluded.source,
                note=excluded.note,
                updated_at=datetime('now','localtime')
        """, (d, is_open, wd, note))
    return closed


def delete_closed_date_rows(cur: sqlite3.Cursor, closed: set[str]) -> dict[str, int]:
    deleted = {}
    if not closed:
        return deleted
    qmarks = ",".join(["?"] * len(closed))
    params = sorted(closed)
    for table, col in DATE_TABLES:
        if not (table_exists(cur, table) and column_exists(cur, table, col)):
            continue
        before = cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({qmarks})", params).fetchone()[0]
        if before:
            cur.execute(f"DELETE FROM {table} WHERE {col} IN ({qmarks})", params)
        deleted[f"{table}.{col}"] = int(before)
    return deleted


def ensure_benchmark_columns(cur: sqlite3.Cursor):
    if not table_exists(cur, "benchmark_daily_equity"):
        cur.execute("""
            CREATE TABLE benchmark_daily_equity (
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


def continuous_prices(raw_rows: list[tuple]) -> list[dict]:
    out = []
    factor = 1.0
    prev_adj = None
    for d, o, c, volume in raw_rows:
        raw_open = float(o or c or 0)
        raw_close = float(c or 0)
        is_valid = 1
        reason = None
        if raw_close <= 0:
            is_valid = 0
            reason = "close<=0_or_null"
            adj_close = prev_adj or 0.0
            adj_open = adj_close
        else:
            adj_close = raw_close / factor
            adj_open = raw_open / factor if raw_open > 0 else adj_close
            if prev_adj and prev_adj > 0:
                ret = adj_close / prev_adj - 1.0
                if abs(ret) > MAX_ABS_DAILY_RETURN:
                    old_adj = adj_close
                    factor = raw_close / prev_adj
                    adj_close = raw_close / factor
                    adj_open = raw_open / factor if raw_open > 0 else adj_close
                    reason = f"price_scale_adjusted raw_jump={ret*100:.1f}% old_adj={old_adj:.4f}"
        if is_valid and adj_close > 0:
            prev_adj = adj_close
        out.append({
            "date": str(d), "raw_open": raw_open, "raw_close": raw_close,
            "adjusted_open": adj_open, "adjusted_close": adj_close,
            "is_valid": is_valid, "reason": reason,
        })
    return out


def rebuild_0050_benchmark(cur: sqlite3.Cursor, start_date: str = "2025-01-01") -> dict:
    ensure_benchmark_columns(cur)
    rows = cur.execute("""
        SELECT o.trade_date, o.open, o.close, o.volume
        FROM ohlcv_daily o
        LEFT JOIN trading_calendar tc ON tc.trade_date=o.trade_date
        WHERE o.code=?
          AND o.trade_date>=?
          AND COALESCE(tc.is_open, 1)=1
          AND strftime('%w', o.trade_date) NOT IN ('0','6')
          AND o.close IS NOT NULL AND o.close>0
        ORDER BY o.trade_date
    """, (BENCHMARK_CODE, start_date)).fetchall()
    prices = continuous_prices(rows)
    first = next((r for r in prices if r["is_valid"] and r["adjusted_open"] > 0), None)
    if not first:
        return {"updated": 0, "start": None, "end": None, "total_return": None, "adjustments": 0}

    buy_date = first["date"]
    buy_price = first["adjusted_open"] or first["adjusted_close"]
    shares = INIT_CASH / buy_price
    cur.execute("DELETE FROM benchmark_daily_equity WHERE benchmark_code=? AND snap_date>=?", (BENCHMARK_CODE, start_date))

    prev_equity = INIT_CASH
    updated = 0
    adjustments = 0
    last_equity = INIT_CASH
    for r in prices:
        if r["date"] < buy_date:
            continue
        price = float(r["adjusted_close"] or buy_price)
        equity = shares * price
        daily_return = (equity / prev_equity - 1) * 100 if prev_equity > 0 else 0.0
        cumulative_return = (equity / INIT_CASH - 1) * 100
        if r["reason"]:
            adjustments += 1
        cur.execute("""
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
        """, (
            BENCHMARK_CODE, r["date"], round(price, 6), shares, round(equity, 2),
            round(daily_return, 4), round(cumulative_return, 4), INIT_CASH,
            int(r["is_valid"]), r["reason"], round(price, 6),
        ))
        prev_equity = equity
        last_equity = equity
        updated += 1

    return {
        "updated": updated,
        "start": buy_date,
        "end": prices[-1]["date"] if prices else None,
        "total_return": round((last_equity / INIT_CASH - 1) * 100, 4),
        "adjustments": adjustments,
    }


def write_report(closed: set[str], deleted: dict[str, int], bench: dict, apply: bool):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "v9_1_p0_fix_report.md"
    lines = []
    lines.append("# V9.1-P0 Daily-only Fix Report\n")
    lines.append(f"- apply：{apply}")
    lines.append("- scope：TWSE 上市股票 + ETF、日級資料；沒有加入分鐘資料表，沒有要求 TPEx")
    lines.append(f"- closed dates detected：{len(closed)}")
    lines.append("\n## Closed Dates\n")
    for d in sorted(closed):
        lines.append(f"- {d}")
    lines.append("\n## Deleted Rows\n")
    for k, v in sorted(deleted.items()):
        lines.append(f"- {k}: {v}")
    lines.append("\n## 0050 Benchmark\n")
    for k, v in bench.items():
        lines.append(f"- {k}: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--apply", action="store_true", help="真正寫入 DB；不加時只做 dry-run")
    ap.add_argument("--backup", action="store_true", help="寫入前備份 quant.db")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    if args.apply and args.backup:
        backup = db_path.with_suffix(f".backup_before_v9_1_p0_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db_path, backup)
        print(f"backup: {backup}")

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        ensure_trading_calendar(cur)
        dates = collect_dates(cur)
        closed = rebuild_trading_calendar(cur, dates)
        deleted = delete_closed_date_rows(cur, closed)
        bench = rebuild_0050_benchmark(cur, start_date="2025-01-01")

        if args.apply:
            con.commit()
            try:
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
        else:
            con.rollback()
        report = write_report(closed, deleted, bench, args.apply)
        print("=== V9.1-P0 fix completed ===")
        print(f"apply: {args.apply}")
        print(f"closed_dates: {len(closed)}")
        print(f"benchmark: {bench}")
        print(f"report: {report}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
