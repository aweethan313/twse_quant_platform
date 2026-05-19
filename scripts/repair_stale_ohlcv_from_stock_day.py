"""
V4.6.2 OHLCV 修復器：用 TWSE STOCK_DAY（個股單月歷史資料）修復 stale 的 ohlcv_daily。

用途：
  1. 不再用 STOCK_DAY_ALL 回填歷史，避免把同一批資料寫到多個日期。
  2. 針對指定股票 / stale report / 觀察清單，逐股逐月抓歷史日 K。
  3. 只 upsert 指定 start-date ~ end-date 區間，不偷看未來。

範例：
  # 先 dry-run 測試
  python -m scripts.repair_stale_ohlcv_from_stock_day --start-date 2026-04-16 --end-date 2026-05-18 --codes 2383,2449,3189,6205

  # 確認 OK 後寫入
  python -m scripts.repair_stale_ohlcv_from_stock_day --start-date 2026-04-16 --end-date 2026-05-18 --codes 2383,2449,3189,6205 --apply

  # 用 stale report 的前 100 檔修復
  python -m scripts.repair_stale_ohlcv_from_stock_day --start-date 2026-04-16 --end-date 2026-05-18 --from-stale-report data/reports/stale_ohlcv_2026-04-16_2026-05-18.csv --limit 100 --apply
"""
from __future__ import annotations

import argparse
import csv
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, OHLCVDaily
from config.settings import settings


DEFAULT_WATCHLIST = [
    "0050", "00981A",
    "2330", "2454", "1802", "2308", "2383", "2449",
    "3037", "3189", "3711", "6205", "2356", "2357",
]

DB_PATH = Path("data/db/quant.db")


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def month_starts(start: date, end: date) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return out


def roc_to_ad(roc_str: str) -> date | None:
    if roc_str is None:
        return None
    s = str(roc_str).strip()
    if not s or "/" not in s:
        return None
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        y = int(parts[0]) + 1911
        m = int(parts[1])
        d = int(parts[2])
        return date(y, m, d)
    except Exception:
        return None


def clean_number(x):
    if x is None:
        return None
    s = str(x).strip()
    if not s or s in {"--", "---", "nan", "None"}:
        return None
    s = (
        s.replace(",", "")
         .replace("+", "")
         .replace("−", "-")
         .replace("－", "-")
         .replace("X", "")
         .replace("x", "")
    )
    try:
        return float(s)
    except Exception:
        return None


def get_codes_from_stale_report(path: Path) -> list[str]:
    codes: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"找不到 stale report: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("code", "")).strip()
            if code:
                codes.append(code)
    return list(dict.fromkeys(codes))


def get_codes_from_db_common_stocks(limit: int | None = None) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT DISTINCT code
            FROM stock_meta
            WHERE code IS NOT NULL
            ORDER BY code
        """)).fetchall()
    finally:
        db.close()

    codes = []
    for (code,) in rows:
        code = str(code).strip()
        # 普通股粗略篩選：四碼數字，排除 00xx ETF/ETN/權證類。
        if len(code) == 4 and code.isdigit() and not code.startswith("0"):
            codes.append(code)

    return codes[:limit] if limit else codes


@dataclass
class FetchResult:
    code: str
    rows: int
    ok: bool
    message: str


class StockDayFetcher:
    def __init__(self):
        self.client = httpx.Client(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TWSEQuantResearch/1.0)"}
        )
        self.last_request = 0.0

    def throttle(self):
        delay = float(getattr(settings, "REQUEST_DELAY_SEC", 0.8) or 0.8)
        elapsed = time.time() - self.last_request
        wait = delay - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.time()

    def fetch_stock_month(self, code: str, year: int, month: int) -> pd.DataFrame | None:
        self.throttle()

        base = getattr(settings, "TWSE_BASE_URL", "https://www.twse.com.tw/exchangeReport")
        url = f"{base}/STOCK_DAY"
        date_str = f"{year}{month:02d}01"

        try:
            r = self.client.get(url, params={
                "response": "json",
                "date": date_str,
                "stockNo": code,
            })
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return None

        if not data or data.get("stat") != "OK" or not data.get("data"):
            return None

        raw_rows = data.get("data", [])
        parsed = []
        prev_close = None

        for row in raw_rows:
            # TWSE STOCK_DAY 常見欄位：
            # 日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數
            if len(row) < 8:
                continue

            d = roc_to_ad(row[0])
            volume = clean_number(row[1])
            value = clean_number(row[2])
            open_ = clean_number(row[3])
            high = clean_number(row[4])
            low = clean_number(row[5])
            close = clean_number(row[6])
            change = clean_number(row[7])

            if d is None or close is None:
                continue

            # 優先用 close / prev_close 算 change_pct，避免原始 change 符號或 X 記號污染。
            change_pct = None
            if prev_close is not None and prev_close > 0:
                change_pct = (close / prev_close - 1.0) * 100.0
                if change is None:
                    change = close - prev_close
            elif change is not None and (close - change) not in (None, 0):
                base_price = close - change
                if base_price:
                    change_pct = change / base_price * 100.0

            parsed.append({
                "code": code,
                "trade_date": d,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "value": value,
                "change": change,
                "change_pct": change_pct,
            })

            prev_close = close

        if not parsed:
            return None
        return pd.DataFrame(parsed)


def load_existing_snapshot(codes: list[str], start: date, end: date) -> dict[tuple[str, str], tuple]:
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT code, trade_date, open, high, low, close, volume, value, change, change_pct
            FROM ohlcv_daily
            WHERE trade_date BETWEEN :s AND :e
              AND code IN :codes
        """).bindparams(), {"s": start, "e": end, "codes": tuple(codes)}).fetchall()
    except Exception:
        # SQLite SQLAlchemy 對 IN :codes 版本可能不穩，改用簡單逐批。
        rows = []
        for code in codes:
            rows.extend(db.execute(text("""
                SELECT code, trade_date, open, high, low, close, volume, value, change, change_pct
                FROM ohlcv_daily
                WHERE trade_date BETWEEN :s AND :e
                  AND code = :code
            """), {"s": start, "e": end, "code": code}).fetchall())
    finally:
        db.close()

    return {(str(r[0]), str(r[1])): tuple(r[2:]) for r in rows}


def upsert_rows(rows: list[dict]):
    if not rows:
        return

    db = SessionLocal()
    try:
        stmt = sqlite_insert(OHLCVDaily).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "trade_date"],
            set_={c: stmt.excluded[c] for c in [
                "open", "high", "low", "close", "volume", "value", "change", "change_pct"
            ]}
        )
        db.execute(stmt)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def repair_codes(codes: list[str], start: date, end: date, apply: bool, max_codes: int | None = None) -> list[FetchResult]:
    if max_codes:
        codes = codes[:max_codes]

    fetcher = StockDayFetcher()
    months = month_starts(start, end)
    results: list[FetchResult] = []

    print("=" * 100)
    print(f"V4.6.2 repair stale OHLCV from TWSE STOCK_DAY")
    print(f"range: {start} ~ {end}")
    print(f"codes: {len(codes)}")
    print(f"apply: {apply}")
    print("=" * 100)

    all_upsert_rows: list[dict] = []

    for i, code in enumerate(codes, 1):
        code_rows: list[dict] = []

        for y, m in months:
            df = fetcher.fetch_stock_month(code, y, m)
            if df is None or df.empty:
                continue

            for _, r in df.iterrows():
                d = r["trade_date"]
                if start <= d <= end:
                    code_rows.append({
                        "code": str(r["code"]),
                        "trade_date": d,
                        "open": r.get("open"),
                        "high": r.get("high"),
                        "low": r.get("low"),
                        "close": r.get("close"),
                        "volume": r.get("volume"),
                        "value": r.get("value"),
                        "change": r.get("change"),
                        "change_pct": r.get("change_pct"),
                    })

        # 同一 code/date 去重
        dedup = {}
        for row in code_rows:
            dedup[(row["code"], row["trade_date"])] = row
        code_rows = list(dedup.values())
        code_rows.sort(key=lambda x: x["trade_date"])

        ok = bool(code_rows)
        msg = "OK" if ok else "NO_DATA"
        results.append(FetchResult(code=code, rows=len(code_rows), ok=ok, message=msg))

        print(f"[{i:4d}/{len(codes):4d}] {code:>8} rows={len(code_rows):2d} {msg}")

        all_upsert_rows.extend(code_rows)

        # 分批寫入，避免一次太大。
        if apply and len(all_upsert_rows) >= 500:
            upsert_rows(all_upsert_rows)
            print(f"  -> wrote batch rows={len(all_upsert_rows)}")
            all_upsert_rows.clear()

    if apply and all_upsert_rows:
        upsert_rows(all_upsert_rows)
        print(f"  -> wrote final rows={len(all_upsert_rows)}")
    elif not apply:
        print("Dry-run only: 未寫入 DB。加 --apply 才會 upsert。")

    ok_count = sum(1 for r in results if r.ok)
    print("=" * 100)
    print(f"finished: ok_codes={ok_count}/{len(codes)}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--codes", default="")
    ap.add_argument("--from-stale-report", default="")
    ap.add_argument("--all-common-stocks", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    start = parse_date(args.start_date)
    end = parse_date(args.end_date)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.from_stale_report:
        codes = get_codes_from_stale_report(Path(args.from_stale_report))
    elif args.all_common_stocks:
        codes = get_codes_from_db_common_stocks(limit=args.limit or None)
    else:
        codes = DEFAULT_WATCHLIST

    # 去重並套 limit
    codes = list(dict.fromkeys(codes))
    if args.limit and not args.all_common_stocks:
        codes = codes[:args.limit]

    if not codes:
        raise SystemExit("沒有可修復的 codes。請使用 --codes / --from-stale-report / --all-common-stocks。")

    repair_codes(codes, start, end, apply=args.apply, max_codes=None)


if __name__ == "__main__":
    main()
