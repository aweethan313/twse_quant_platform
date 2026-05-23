from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, OHLCV1Min


ENCODINGS = ["utf-8-sig", "utf-8", "cp950", "big5"]

COLUMN_ALIASES = {
    "code": [
        "code", "stock_id", "symbol", "ticker", "證券代號", "股票代號", "代號",
    ],
    "date": [
        "date", "trade_date", "交易日", "日期",
    ],
    "datetime": [
        "ts", "timestamp", "datetime", "date_time", "日期時間", "交易時間", "成交時間",
    ],
    "time": [
        "time", "時間", "分時", "成交時間",
    ],
    "open": [
        "open", "開盤", "開盤價", "開",
    ],
    "high": [
        "high", "最高", "最高價", "高",
    ],
    "low": [
        "low", "最低", "最低價", "低",
    ],
    "close": [
        "close", "price", "收盤", "收盤價", "成交價", "現價", "成交",
    ],
    "volume": [
        "volume", "vol", "成交量", "成交股數", "成交張數", "量",
    ],
    "buy_vol": [
        "buy_vol", "buy_volume", "外盤量", "外盤", "買量",
    ],
    "sell_vol": [
        "sell_vol", "sell_volume", "內盤量", "內盤", "賣量",
    ],
}


@dataclass
class ImportIssue:
    file: str
    row_no: int
    issue_type: str
    message: str


@dataclass
class ParsedFileResult:
    file: Path
    encoding: str
    total_rows: int
    valid_rows: int
    issues: list[ImportIssue]
    rows: list[dict[str, Any]]


def normalize_col(name: str) -> str:
    return (
        str(name)
        .strip()
        .replace("\ufeff", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .lower()
    )


def build_column_map(fieldnames: list[str]) -> dict[str, str]:
    normalized_to_original = {normalize_col(c): c for c in fieldnames}
    result: dict[str, str] = {}

    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = normalize_col(alias)
            if key in normalized_to_original:
                result[canonical] = normalized_to_original[key]
                break

    return result


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


def safe_float(x: Any) -> float | None:
    s = safe_str(x).replace(",", "")

    if s in {"", "-", "--", "nan", "NaN", "None", "null"}:
        return None

    # 有些資料會帶單位或百分比，成交量/價格先只保留數字、小數點、正負號。
    s = re.sub(r"[^0-9.\-]", "", s)

    if s in {"", "-", ".", "-."}:
        return None

    try:
        return float(s)
    except ValueError:
        return None


def parse_date_like(x: Any) -> date | None:
    s = safe_str(x)
    if not s:
        return None

    s = s.replace("/", "-").replace(".", "-")

    # 20260522
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None

    # 2026-05-22...
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    return None


def parse_time_like(x: Any) -> time | None:
    s = safe_str(x)
    if not s:
        return None

    # 09:01 / 09:01:00
    for fmt in ["%H:%M:%S", "%H:%M"]:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass

    # 0901 / 090100
    if re.fullmatch(r"\d{4}", s):
        try:
            return datetime.strptime(s, "%H%M").time()
        except ValueError:
            return None

    if re.fullmatch(r"\d{6}", s):
        try:
            return datetime.strptime(s, "%H%M%S").time()
        except ValueError:
            return None

    # 有些完整 datetime 欄位會被放到 time 欄
    dt = parse_datetime_like(s)
    if dt:
        return dt.time()

    return None


def parse_datetime_like(x: Any) -> datetime | None:
    s = safe_str(x)
    if not s:
        return None

    s = s.replace("/", "-").replace("T", " ")

    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y%m%d %H:%M:%S",
        "%Y%m%d %H:%M",
        "%Y%m%d%H%M%S",
        "%Y%m%d%H%M",
    ]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def infer_code_and_date_from_filename(path: Path) -> tuple[str | None, date | None]:
    stem = path.stem

    trade_date: date | None = None
    code: str | None = None

    # date: 2026-05-22 / 2026_05_22 / 20260522
    date_match = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", stem)
    if date_match:
        y, m, d = map(int, date_match.groups())
        try:
            trade_date = date(y, m, d)
        except ValueError:
            trade_date = None

    tokens = re.split(r"[_\-\s]+", stem)

    for token in tokens:
        token = token.strip().upper()
        if re.fullmatch(r"\d{4,5}[A-Z]?", token):
            code = token
            break

    # fallback: filename 開頭是 2330xxxx
    if code is None:
        m = re.match(r"^(\d{4,5}[A-Z]?)", stem.upper())
        if m:
            code = m.group(1)

    return code, trade_date


def read_csv_rows(path: Path) -> tuple[list[dict[str, Any]], str]:
    last_error: Exception | None = None

    for enc in ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)

                try:
                    dialect = csv.Sniffer().sniff(sample)
                except csv.Error:
                    dialect = csv.excel

                reader = csv.DictReader(f, dialect=dialect)
                rows = list(reader)

            return rows, enc

        except Exception as e:
            last_error = e

    raise RuntimeError(f"讀取 CSV 失敗：{path}，最後錯誤：{last_error}")


def parse_one_file(
    path: Path,
    forced_code: str | None,
    forced_trade_date: date | None,
) -> ParsedFileResult:
    raw_rows, encoding = read_csv_rows(path)
    issues: list[ImportIssue] = []
    parsed_rows: list[dict[str, Any]] = []

    if not raw_rows:
        return ParsedFileResult(
            file=path,
            encoding=encoding,
            total_rows=0,
            valid_rows=0,
            issues=[
                ImportIssue(
                    file=str(path),
                    row_no=0,
                    issue_type="empty_file",
                    message="CSV 沒有資料列",
                )
            ],
            rows=[],
        )

    fieldnames = list(raw_rows[0].keys())
    colmap = build_column_map(fieldnames)

    filename_code, filename_date = infer_code_and_date_from_filename(path)

    base_code = forced_code or filename_code
    base_trade_date = forced_trade_date or filename_date

    if "close" not in colmap:
        issues.append(
            ImportIssue(
                file=str(path),
                row_no=0,
                issue_type="missing_close_column",
                message=f"找不到 close/price/成交價 欄位，欄位={fieldnames}",
            )
        )
        return ParsedFileResult(path, encoding, len(raw_rows), 0, issues, [])

    for idx, r in enumerate(raw_rows, start=2):
        row_code = safe_str(r.get(colmap.get("code", ""), ""))
        code = forced_code or row_code or base_code

        if not code:
            issues.append(
                ImportIssue(str(path), idx, "missing_code", "無法從欄位或檔名推得股票代號")
            )
            continue

        code = code.upper()

        row_date = None
        if "date" in colmap:
            row_date = parse_date_like(r.get(colmap["date"]))

        trade_date = forced_trade_date or row_date or base_trade_date

        dt = None
        if "datetime" in colmap:
            dt = parse_datetime_like(r.get(colmap["datetime"]))

        if dt is None:
            row_time = None
            if "time" in colmap:
                row_time = parse_time_like(r.get(colmap["time"]))

            if trade_date is not None and row_time is not None:
                dt = datetime.combine(trade_date, row_time)

        if dt is None:
            issues.append(
                ImportIssue(str(path), idx, "invalid_timestamp", "無法解析時間戳")
            )
            continue

        close = safe_float(r.get(colmap["close"]))
        if close is None:
            issues.append(
                ImportIssue(str(path), idx, "invalid_close", "無法解析 close/price")
            )
            continue

        open_price = safe_float(r.get(colmap["open"])) if "open" in colmap else None
        high = safe_float(r.get(colmap["high"])) if "high" in colmap else None
        low = safe_float(r.get(colmap["low"])) if "low" in colmap else None
        volume = safe_float(r.get(colmap["volume"])) if "volume" in colmap else None
        buy_vol = safe_float(r.get(colmap["buy_vol"])) if "buy_vol" in colmap else None
        sell_vol = safe_float(r.get(colmap["sell_vol"])) if "sell_vol" in colmap else None

        if open_price is None:
            open_price = close
        if high is None:
            high = max(open_price, close)
        if low is None:
            low = min(open_price, close)

        parsed_rows.append(
            {
                "code": code,
                "ts": dt.replace(second=0, microsecond=0),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume or 0.0,
                "buy_vol": buy_vol or 0.0,
                "sell_vol": sell_vol or 0.0,
            }
        )

    return ParsedFileResult(
        file=path,
        encoding=encoding,
        total_rows=len(raw_rows),
        valid_rows=len(parsed_rows),
        issues=issues,
        rows=parsed_rows,
    )


def upsert_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    db = SessionLocal()

    try:
        stmt = sqlite_insert(OHLCV1Min).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "buy_vol": stmt.excluded.buy_vol,
                "sell_vol": stmt.excluded.sell_vol,
            },
        )

        db.execute(stmt)
        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def count_db_rows() -> int:
    db = SessionLocal()

    try:
        return int(db.query(OHLCV1Min).count())
    finally:
        db.close()


def find_input_files(source_dir: Path) -> list[Path]:
    if not source_dir.exists():
        return []

    return sorted(
        p
        for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".csv", ".txt"}
    )


def write_reports(
    results: list[ParsedFileResult],
    issues: list[ImportIssue],
    out_dir: Path,
    apply: bool,
    before_count: int,
    after_count: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    issue_csv = out_dir / "s8_v2_3_minute_import_issues.csv"
    report_md = out_dir / "s8_v2_3_minute_import_report.md"

    with issue_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["file", "row_no", "issue_type", "message"],
        )
        writer.writeheader()
        for x in issues:
            writer.writerow(
                {
                    "file": x.file,
                    "row_no": x.row_no,
                    "issue_type": x.issue_type,
                    "message": x.message,
                }
            )

    total_files = len(results)
    total_raw_rows = sum(r.total_rows for r in results)
    total_valid_rows = sum(r.valid_rows for r in results)

    lines: list[str] = []
    lines.append("# S8 v2-3 Minute CSV Import Report")
    lines.append("")
    lines.append(f"- Apply: `{apply}`")
    lines.append(f"- Files scanned: `{total_files}`")
    lines.append(f"- Raw rows: `{total_raw_rows}`")
    lines.append(f"- Valid rows: `{total_valid_rows}`")
    lines.append(f"- Issues: `{len(issues)}`")
    lines.append(f"- DB rows before: `{before_count}`")
    lines.append(f"- DB rows after: `{after_count}`")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("| file | encoding | raw_rows | valid_rows |")
    lines.append("|---|---|---:|---:|")

    for r in results:
        lines.append(
            f"| {r.file} | {r.encoding} | {r.total_rows} | {r.valid_rows} |"
        )

    lines.append("")
    lines.append("## Top Issues")
    lines.append("")

    if not issues:
        lines.append("No issues.")
    else:
        lines.append("| file | row_no | issue_type | message |")
        lines.append("|---|---:|---|---|")
        for x in issues[:80]:
            lines.append(
                f"| {x.file} | {x.row_no} | {x.issue_type} | {x.message} |"
            )

    report_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"Report: {report_md}")
    print(f"Issues: {issue_csv}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="data/raw/minute")
    parser.add_argument("--out-dir", default="data/reports/minute_import")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--code", default=None, help="強制指定股票代號")
    parser.add_argument("--trade-date", default=None, help="強制指定交易日 YYYY-MM-DD")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    out_dir = Path(args.out_dir)

    forced_trade_date = parse_date_like(args.trade_date) if args.trade_date else None

    files = find_input_files(source_dir)

    print("S8 v2-3 after-hours minute CSV importer")
    print("=" * 80)
    print(f"source_dir: {source_dir}")
    print(f"files found: {len(files)}")
    print(f"apply: {args.apply}")
    print("=" * 80)

    before_count = count_db_rows()

    results: list[ParsedFileResult] = []
    all_issues: list[ImportIssue] = []
    all_rows: list[dict[str, Any]] = []

    for path in files:
        result = parse_one_file(
            path=path,
            forced_code=args.code,
            forced_trade_date=forced_trade_date,
        )
        results.append(result)
        all_issues.extend(result.issues)
        all_rows.extend(result.rows)

        print(
            f"[FILE] {path} | encoding={result.encoding} | "
            f"raw={result.total_rows} | valid={result.valid_rows} | issues={len(result.issues)}"
        )

    if args.apply:
        upsert_rows(all_rows)

    after_count = count_db_rows()

    print("=" * 80)
    print(f"DB rows before: {before_count}")
    print(f"DB rows after: {after_count}")
    print(f"valid rows parsed: {len(all_rows)}")
    print(f"issues: {len(all_issues)}")

    write_reports(
        results=results,
        issues=all_issues,
        out_dir=out_dir,
        apply=args.apply,
        before_count=before_count,
        after_count=after_count,
    )


if __name__ == "__main__":
    main()
