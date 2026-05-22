from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Any


DEFAULT_DB_PATHS = [
    Path("data/db/quant.db"),
    Path("quant.db"),
    Path("data/quant.db"),
]


CODE_COLS = ["code", "stock_id", "symbol", "ticker"]
TS_COLS = ["ts", "timestamp", "datetime", "time", "signal_ts", "decision_ts", "created_at"]
ACTION_COLS = ["signal", "action", "decision", "side", "direction", "recommendation"]
PRICE_COLS = ["close", "price", "成交價"]
VOLUME_COLS = ["volume", "成交股數", "成交量"]


@dataclass
class TableInfo:
    name: str
    columns: list[str]


@dataclass
class SignalRow:
    source_table: str
    rowid: int
    code: str
    ts_raw: str
    ts: datetime | None
    action: str
    extra: dict[str, Any]


@dataclass
class AuditIssue:
    severity: str
    issue_type: str
    source_table: str
    rowid: int | str
    code: str
    ts: str
    action: str
    message: str


def resolve_db_path(arg_path: str | None) -> Path:
    if arg_path:
        path = Path(arg_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"找不到指定資料庫：{path}")

    for path in DEFAULT_DB_PATHS:
        if path.exists():
            return path

    raise FileNotFoundError(
        "找不到資料庫。請用 --db-path 指定，例如：--db-path data/db/quant.db"
    )


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def list_tables(conn: sqlite3.Connection) -> list[TableInfo]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    tables: list[TableInfo] = []
    for r in rows:
        name = r["name"]
        cols = [
            c["name"]
            for c in conn.execute(f"PRAGMA table_info({quote_ident(name)})").fetchall()
        ]
        tables.append(TableInfo(name=name, columns=cols))

    return tables


def pick_col(columns: list[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower(): c for c in columns}

    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]

    return None


def looks_like_signal_table(t: TableInfo) -> bool:
    name = t.name.lower()

    if name in {"sqlite_sequence"}:
        return False

    # daily_scores 是每日評分 / 每日選股訊號，不是盤中 timing source。
    # S8 v2-1 先專注 audit intraday / trade timing，不把 daily_scores 混進來。
    if name == "daily_scores":
        return False

    has_signal_name = any(
        k in name
        for k in ["signal", "decision", "alert", "order", "trade", "log"]
    )
    has_code = pick_col(t.columns, CODE_COLS) is not None
    has_ts = pick_col(t.columns, TS_COLS) is not None
    has_action = pick_col(t.columns, ACTION_COLS) is not None

    return has_signal_name and has_code and has_ts and has_action


def looks_like_minute_table(t: TableInfo) -> bool:
    name = t.name.lower()

    has_minute_name = any(k in name for k in ["ohlcv_1min", "minute", "intraday"])
    has_code = pick_col(t.columns, CODE_COLS) is not None
    has_ts = pick_col(t.columns, TS_COLS) is not None
    has_price = pick_col(t.columns, PRICE_COLS) is not None

    return has_minute_name and has_code and has_ts and has_price


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None

    candidates = [
        s,
        s.replace("Z", ""),
        s.replace("/", "-"),
        s.replace("T", " "),
    ]

    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ]

    for x in candidates:
        try:
            return datetime.fromisoformat(x)
        except ValueError:
            pass

        for fmt in fmts:
            try:
                return datetime.strptime(x, fmt)
            except ValueError:
                pass

    return None


def normalize_action(value: Any) -> str:
    s = str(value or "").strip().upper()

    if s in {"B", "BUY", "LONG", "買", "買進"}:
        return "BUY"

    if s in {"S", "SELL", "SHORT", "賣", "賣出"}:
        return "SELL"

    if s in {"H", "HOLD", "WAIT", "觀望"}:
        return "HOLD"

    return s or "UNKNOWN"


def load_signals(
    conn: sqlite3.Connection,
    table: TableInfo,
    limit: int,
) -> list[SignalRow]:
    code_col = pick_col(table.columns, CODE_COLS)
    ts_col = pick_col(table.columns, TS_COLS)
    action_col = pick_col(table.columns, ACTION_COLS)

    if not code_col or not ts_col or not action_col:
        return []

    sql = f"""
        SELECT rowid AS audit_rowid, *
        FROM {quote_ident(table.name)}
        ORDER BY {quote_ident(ts_col)} DESC
        LIMIT ?
    """

    rows = conn.execute(sql, (limit,)).fetchall()

    signals: list[SignalRow] = []

    for r in rows:
        ts_raw = str(r[ts_col]) if r[ts_col] is not None else ""

        signals.append(
            SignalRow(
                source_table=table.name,
                rowid=int(r["audit_rowid"]),
                code=str(r[code_col]).strip(),
                ts_raw=ts_raw,
                ts=parse_dt(ts_raw),
                action=normalize_action(r[action_col]),
                extra={k: r[k] for k in r.keys() if k != "audit_rowid"},
            )
        )

    return signals


def get_minute_table_schema(t: TableInfo) -> dict[str, str | None]:
    return {
        "code": pick_col(t.columns, CODE_COLS),
        "ts": pick_col(t.columns, TS_COLS),
        "close": pick_col(t.columns, PRICE_COLS),
        "volume": pick_col(t.columns, VOLUME_COLS),
    }


def find_previous_completed_bar(
    conn: sqlite3.Connection,
    minute_table: TableInfo,
    code: str,
    signal_ts: datetime,
) -> sqlite3.Row | None:
    schema = get_minute_table_schema(minute_table)
    code_col = schema["code"]
    ts_col = schema["ts"]

    if not code_col or not ts_col:
        return None

    # 保守假設：
    # signal 當下只能使用上一根「已完成」的一分鐘 K。
    # 例如 09:31:00 做決策，最多只能用到 09:30:00 那根。
    latest_allowed_ts = signal_ts.replace(second=0, microsecond=0) - timedelta(minutes=1)

    sql = f"""
        SELECT *
        FROM {quote_ident(minute_table.name)}
        WHERE {quote_ident(code_col)} = ?
          AND {quote_ident(ts_col)} <= ?
        ORDER BY {quote_ident(ts_col)} DESC
        LIMIT 1
    """

    return conn.execute(
        sql,
        (code, latest_allowed_ts.isoformat(sep=" ")),
    ).fetchone()


def find_same_minute_bar(
    conn: sqlite3.Connection,
    minute_table: TableInfo,
    code: str,
    signal_ts: datetime,
) -> sqlite3.Row | None:
    schema = get_minute_table_schema(minute_table)
    code_col = schema["code"]
    ts_col = schema["ts"]

    if not code_col or not ts_col:
        return None

    minute_start = signal_ts.replace(second=0, microsecond=0)

    sql = f"""
        SELECT *
        FROM {quote_ident(minute_table.name)}
        WHERE {quote_ident(code_col)} = ?
          AND {quote_ident(ts_col)} = ?
        LIMIT 1
    """

    return conn.execute(
        sql,
        (code, minute_start.isoformat(sep=" ")),
    ).fetchone()


def is_regular_intraday_time(dt: datetime) -> bool:
    return time(9, 0) <= dt.time() <= time(13, 30)


def make_issue(
    severity: str,
    issue_type: str,
    s: SignalRow,
    message: str,
) -> AuditIssue:
    return AuditIssue(
        severity=severity,
        issue_type=issue_type,
        source_table=s.source_table,
        rowid=s.rowid,
        code=s.code,
        ts=s.ts.isoformat(sep=" ") if s.ts else s.ts_raw,
        action=s.action,
        message=message,
    )


def audit_signals(
    conn: sqlite3.Connection,
    signals: list[SignalRow],
    minute_tables: list[TableInfo],
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []

    seen_key: dict[tuple[str, str, str, str], int] = {}
    daily_actions: dict[tuple[str, str], list[SignalRow]] = {}

    minute_table = minute_tables[0] if minute_tables else None

    for s in signals:
        ts_text = s.ts.isoformat(sep=" ") if s.ts else s.ts_raw

        if not s.code:
            issues.append(
                make_issue(
                    "HIGH",
                    "missing_code",
                    s,
                    "signal 缺少股票代號",
                )
            )

        if s.ts is None:
            issues.append(
                make_issue(
                    "HIGH",
                    "invalid_timestamp",
                    s,
                    f"無法解析時間戳：{s.ts_raw}",
                )
            )
            continue

        if not is_regular_intraday_time(s.ts):
            issues.append(
                make_issue(
                    "MEDIUM",
                    "outside_regular_session",
                    s,
                    "signal 不在一般盤中時間 09:00~13:30 內",
                )
            )

        key = (s.source_table, s.code, ts_text, s.action)

        if key in seen_key:
            issues.append(
                make_issue(
                    "MEDIUM",
                    "duplicate_signal",
                    s,
                    f"同股票、同時間、同動作重複訊號；前一筆 rowid={seen_key[key]}",
                )
            )
        else:
            seen_key[key] = s.rowid

        day_key = (s.code, s.ts.date().isoformat())
        daily_actions.setdefault(day_key, []).append(s)

        if minute_table is None:
            issues.append(
                make_issue(
                    "HIGH",
                    "missing_minute_table",
                    s,
                    "找不到可用的一分鐘 K 資料表，無法驗證 signal 是否偷看未來",
                )
            )
            continue

        prev_bar = find_previous_completed_bar(conn, minute_table, s.code, s.ts)

        if prev_bar is None:
            issues.append(
                make_issue(
                    "HIGH",
                    "no_previous_completed_bar",
                    s,
                    "signal 發生前找不到上一根已完成分 K；策略可能沒有可用當下資料",
                )
            )

        same_minute_bar = find_same_minute_bar(conn, minute_table, s.code, s.ts)

        if same_minute_bar is not None and s.ts.second == 0:
            issues.append(
                make_issue(
                    "MEDIUM",
                    "same_minute_close_risk",
                    s,
                    "signal 時間剛好等於該分鐘 K 時間；若使用該分鐘 close，可能偷看未來",
                )
            )

    for (code, day), rows in daily_actions.items():
        ordered_rows = sorted(
            rows,
            key=lambda r: (r.ts or datetime.max, r.rowid),
        )

        first_buy: SignalRow | None = None
        violation_sell: SignalRow | None = None

        for r in ordered_rows:
            if r.action == "BUY" and first_buy is None:
                first_buy = r
                continue

            if r.action == "SELL" and first_buy is not None:
                violation_sell = r
                break

        if first_buy is not None and violation_sell is not None:
            issues.append(
                AuditIssue(
                    severity="HIGH",
                    issue_type="same_day_buy_then_sell_violation",
                    source_table="multiple",
                    rowid=f"{first_buy.rowid},{violation_sell.rowid}",
                    code=code,
                    ts=day,
                    action="BUY->SELL",
                    message=(
                        "同一交易日同股票出現 BUY 後又 SELL；"
                        "這會賣到當日買進部位，違反不能先買後賣規則"
                    ),
                )
            )

    return issues


def write_csv(path: Path, issues: list[AuditIssue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "severity",
                "issue_type",
                "source_table",
                "rowid",
                "code",
                "ts",
                "action",
                "message",
            ],
        )
        writer.writeheader()

        for x in issues:
            writer.writerow(x.__dict__)


def write_md(
    path: Path,
    db_path: Path,
    signal_tables: list[TableInfo],
    minute_tables: list[TableInfo],
    signal_count: int,
    issues: list[AuditIssue],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    high = sum(1 for x in issues if x.severity == "HIGH")
    medium = sum(1 for x in issues if x.severity == "MEDIUM")
    low = sum(1 for x in issues if x.severity == "LOW")

    issue_type_counts: dict[str, int] = {}

    for x in issues:
        issue_type_counts[x.issue_type] = issue_type_counts.get(x.issue_type, 0) + 1

    lines: list[str] = []

    lines.append("# S8 v2-1 Intraday Timing Signal Audit")
    lines.append("")
    lines.append(f"- DB: `{db_path}`")
    lines.append(f"- Signal rows scanned: `{signal_count}`")
    lines.append(f"- Signal tables: `{', '.join(t.name for t in signal_tables) or 'None'}`")
    lines.append(f"- Minute tables: `{', '.join(t.name for t in minute_tables) or 'None'}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- HIGH: `{high}`")
    lines.append(f"- MEDIUM: `{medium}`")
    lines.append(f"- LOW: `{low}`")
    lines.append(f"- Total issues: `{len(issues)}`")
    lines.append("")

    lines.append("## Issue Type Counts")
    lines.append("")

    if issue_type_counts:
        lines.append("| issue_type | count |")
        lines.append("|---|---:|")

        for k, v in sorted(
            issue_type_counts.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            lines.append(f"| {k} | {v} |")
    else:
        lines.append("No issues found.")

    lines.append("")
    lines.append("## Top Issues")
    lines.append("")

    if issues:
        lines.append("| severity | issue_type | table | rowid | code | ts | action | message |")
        lines.append("|---|---|---|---:|---|---|---|---|")

        for x in issues[:80]:
            lines.append(
                f"| {x.severity} | {x.issue_type} | {x.source_table} | {x.rowid} | "
                f"{x.code} | {x.ts} | {x.action} | {x.message} |"
            )
    else:
        lines.append("No issues found.")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument(
        "--out-dir",
        default="data/reports",
        help="輸出報告資料夾",
    )

    args = parser.parse_args()

    db_path = resolve_db_path(args.db_path)
    out_dir = Path(args.out_dir)

    conn = connect_db(db_path)

    try:
        tables = list_tables(conn)

        signal_tables = [t for t in tables if looks_like_signal_table(t)]
        minute_tables = [t for t in tables if looks_like_minute_table(t)]

        all_signals: list[SignalRow] = []

        for t in signal_tables:
            all_signals.extend(load_signals(conn, t, args.limit))

        issues = audit_signals(conn, all_signals, minute_tables)

        csv_path = out_dir / "s8_v2_1_intraday_timing_signal_audit_issues.csv"
        md_path = out_dir / "s8_v2_1_intraday_timing_signal_audit_report.md"

        write_csv(csv_path, issues)
        write_md(
            path=md_path,
            db_path=db_path,
            signal_tables=signal_tables,
            minute_tables=minute_tables,
            signal_count=len(all_signals),
            issues=issues,
        )

        print("S8 v2-1 intraday timing signal audit finished.")
        print(f"DB: {db_path}")
        print(f"Signal tables: {[t.name for t in signal_tables]}")
        print(f"Minute tables: {[t.name for t in minute_tables]}")
        print(f"Signal rows scanned: {len(all_signals)}")
        print(f"Issues found: {len(issues)}")
        print(f"CSV: {csv_path}")
        print(f"MD: {md_path}")

        if not signal_tables:
            print("[WARN] 找不到 signal / decision / alert / order / trade 類型資料表。")

        if not minute_tables:
            print("[WARN] 找不到 minute / intraday / ohlcv_1min 類型資料表。")

    finally:
        conn.close()


if __name__ == "__main__":
    main()