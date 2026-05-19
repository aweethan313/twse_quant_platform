"""
scripts/backfill_valuation_daily.py

補抓每日 PE / PB / 殖利率資料。

資料來源：
1. TWSE：上市個股日本益比、殖利率及股價淨值比
2. TPEx：上櫃股票個股本益比、殖利率、股價淨值比

No-lookahead 設計：
- 每一筆 valuation_daily 都有 valuation_date
- scorer 只使用 valuation_date <= score_date 的資料
"""

import os
import re
import sys
import time
import argparse
import sqlite3
from io import StringIO
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from backend.models.database import init_db


def clean_number(x) -> Optional[float]:
    if x is None:
        return None

    s = str(x).strip()
    s = s.replace(",", "")
    s = s.replace("%", "")
    s = s.replace("--", "")
    s = s.replace("—", "")
    s = s.replace("N/A", "")
    s = s.replace("nan", "")
    s = s.replace("NaN", "")

    if s in ("", "-", "None", "null"):
        return None

    try:
        return float(s)
    except ValueError:
        return None


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def to_roc_date(d: date) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def parse_fiscal_year_quarter(x):
    """
    支援：
    - 114/4
    - 114年第4季
    - 2025/4
    """
    if x is None:
        return None, None

    s = str(x).strip()
    nums = re.findall(r"\d+", s)

    if len(nums) < 2:
        return None, None

    y = int(nums[0])
    q = int(nums[1])

    if y < 1911:
        y += 1911

    if q not in (1, 2, 3, 4):
        return y, None

    return y, q


def ensure_schema(conn: sqlite3.Connection):
    """
    既有 DB 不會因為 ORM class 新增就自動建表；
    所以這裡直接 CREATE TABLE IF NOT EXISTS。
    """
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS valuation_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            valuation_date DATE NOT NULL,
            close REAL,
            dividend_yield REAL,
            pe REAL,
            pb REAL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            market TEXT,
            UNIQUE(code, valuation_date)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_valuation_code_date
        ON valuation_daily(code, valuation_date)
    """)

    conn.commit()


def find_col(df: pd.DataFrame, keywords_list):
    for col in df.columns:
        name = str(col)
        for keywords in keywords_list:
            if all(k in name for k in keywords):
                return col
    return None


def fetch_twse_valuation(d: date) -> pd.DataFrame:
    """
    TWSE 上市 PE/PB。
    """
    url = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
    params = {
        "response": "json",
        "date": d.strftime("%Y%m%d"),
        "selectType": "ALL",
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
    }

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        r = client.get(url, params=params)

    if r.status_code != 200:
        print(f"[TWSE SKIP] {d} status={r.status_code}")
        return pd.DataFrame()

    data = r.json()

    if data.get("stat") != "OK" or not data.get("data"):
        print(f"[TWSE SKIP] {d} stat={data.get('stat')}")
        return pd.DataFrame()

    fields = data.get("fields", [])
    raw_df = pd.DataFrame(data["data"], columns=fields[:len(data["data"][0])])

    code_col = find_col(raw_df, [["證券", "代號"], ["代號"]])
    close_col = find_col(raw_df, [["收盤"]])
    dy_col = find_col(raw_df, [["殖利率"]])
    pe_col = find_col(raw_df, [["本益比"]])
    pb_col = find_col(raw_df, [["股價淨值比"]])
    fq_col = find_col(raw_df, [["財報"]])

    if code_col is None or pe_col is None or pb_col is None:
        print(f"[TWSE SKIP] {d} missing columns: {list(raw_df.columns)}")
        return pd.DataFrame()

    rows = []

    for _, r in raw_df.iterrows():
        code = str(r.get(code_col, "")).strip()

        if not re.fullmatch(r"[0-9]{4}[A-Z]?", code):
            continue

        fiscal_year, fiscal_quarter = parse_fiscal_year_quarter(r.get(fq_col)) if fq_col else (None, None)

        rows.append({
            "code": code,
            "valuation_date": d.isoformat(),
            "close": clean_number(r.get(close_col)) if close_col else None,
            "dividend_yield": clean_number(r.get(dy_col)) if dy_col else None,
            "pe": clean_number(r.get(pe_col)),
            "pb": clean_number(r.get(pb_col)),
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "market": "TWSE",
        })

    out = pd.DataFrame(rows)
    print(f"[TWSE OK] {d} rows={len(out)}")
    return out


def fetch_tpex_valuation(d: date) -> pd.DataFrame:
    """
    TPEx 上櫃 PE/PB。

    注意：
    TPEx 這個舊 endpoint 有時會調整參數格式。
    如果抓不到，先不讓整個流程失敗，只回傳空表。
    """
    url = "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"
    params = {
        "l": "zh-tw",
        "d": to_roc_date(d),
        "c": "",
        "s": "0,asc",
        "o": "htm",
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
    }

    try:
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            r = client.get(url, params=params)

        if r.status_code != 200:
            print(f"[TPEX SKIP] {d} status={r.status_code}")
            return pd.DataFrame()

        html = r.text

        if "查無資料" in html or len(html.strip()) < 500:
            print(f"[TPEX SKIP] {d} empty")
            return pd.DataFrame()

        tables = pd.read_html(StringIO(html))

    except Exception as e:
        print(f"[TPEX FAIL] {d} {e}")
        return pd.DataFrame()

    records = []

    for table in tables:
        if table is None or table.empty:
            continue

        # 典型欄位：股票代號、名稱、本益比、每股股利、股利年度、殖利率、股價淨值比
        if table.shape[1] < 6:
            continue

        for _, row in table.iterrows():
            values = [str(x).strip() for x in row.tolist()]

            code_idx = None
            for i, v in enumerate(values[:3]):
                if re.fullmatch(r"[0-9]{4}[A-Z]?", v):
                    code_idx = i
                    break

            if code_idx is None:
                continue

            # 依 TPEx 頁面常見順序解析
            code = values[code_idx]
            pe = clean_number(values[code_idx + 2]) if len(values) > code_idx + 2 else None
            dividend_yield = clean_number(values[code_idx + 5]) if len(values) > code_idx + 5 else None
            pb = clean_number(values[code_idx + 6]) if len(values) > code_idx + 6 else None

            records.append({
                "code": code,
                "valuation_date": d.isoformat(),
                "close": None,
                "dividend_yield": dividend_yield,
                "pe": pe,
                "pb": pb,
                "fiscal_year": None,
                "fiscal_quarter": None,
                "market": "TPEX",
            })

    out = pd.DataFrame(records)

    if out.empty:
        print(f"[TPEX SKIP] {d} parsed empty")
    else:
        out = out.drop_duplicates(subset=["code", "valuation_date"], keep="last")
        print(f"[TPEX OK] {d} rows={len(out)}")

    return out


def upsert_valuation(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0

    sql = """
        INSERT INTO valuation_daily
            (code, valuation_date, close, dividend_yield, pe, pb, fiscal_year, fiscal_quarter, market)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, valuation_date)
        DO UPDATE SET
            close = excluded.close,
            dividend_yield = excluded.dividend_yield,
            pe = excluded.pe,
            pb = excluded.pb,
            fiscal_year = excluded.fiscal_year,
            fiscal_quarter = excluded.fiscal_quarter,
            market = excluded.market
    """

    rows = []

    for _, r in df.iterrows():
        rows.append((
            str(r["code"]),
            str(r["valuation_date"]),
            None if pd.isna(r["close"]) else float(r["close"]),
            None if pd.isna(r["dividend_yield"]) else float(r["dividend_yield"]),
            None if pd.isna(r["pe"]) else float(r["pe"]),
            None if pd.isna(r["pb"]) else float(r["pb"]),
            None if pd.isna(r["fiscal_year"]) else int(r["fiscal_year"]),
            None if pd.isna(r["fiscal_quarter"]) else int(r["fiscal_quarter"]),
            str(r["market"]),
        ))

    conn.executemany(sql, rows)
    conn.commit()

    return len(rows)


def get_latest_trading_dates(conn: sqlite3.Connection, limit: int) -> list[date]:
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT trade_date
        FROM ohlcv_daily
        ORDER BY trade_date DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    dates = [parse_date(str(r[0])) for r in rows]
    dates.sort()
    return dates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--delay-sec", type=float, default=1.0)
    args = parser.parse_args()

    init_db()

    conn = sqlite3.connect(settings.DB_PATH)
    ensure_schema(conn)

    try:
        if args.start_date and args.end_date:
            start = parse_date(args.start_date)
            end = parse_date(args.end_date)

            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT trade_date
                FROM ohlcv_daily
                WHERE trade_date BETWEEN ? AND ?
                ORDER BY trade_date
            """, (start.isoformat(), end.isoformat()))
            trade_dates = [parse_date(str(r[0])) for r in cur.fetchall()]
        else:
            trade_dates = get_latest_trading_dates(conn, args.limit)

        print("=" * 70)
        print("Backfill valuation_daily")
        print(f"DB    : {settings.DB_PATH}")
        print(f"dates : {len(trade_dates)}")
        if trade_dates:
            print(f"range : {trade_dates[0]} ~ {trade_dates[-1]}")
        print("=" * 70)

        total = 0

        for d in trade_dates:
            frames = []

            try:
                twse_df = fetch_twse_valuation(d)
                if twse_df is not None and not twse_df.empty:
                    frames.append(twse_df)
            except Exception as e:
                print(f"[TWSE FAIL] {d} {e}")

            time.sleep(args.delay_sec)

            try:
                tpex_df = fetch_tpex_valuation(d)
                if tpex_df is not None and not tpex_df.empty:
                    frames.append(tpex_df)
            except Exception as e:
                print(f"[TPEX FAIL] {d} {e}")

            if frames:
                all_df = pd.concat(frames, ignore_index=True)
                all_df = all_df.drop_duplicates(subset=["code", "valuation_date"], keep="last")
                n = upsert_valuation(conn, all_df)
                total += n
                print(f"[DAY DONE] {d} upsert={n}")
            else:
                print(f"[DAY EMPTY] {d}")

            time.sleep(args.delay_sec)

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM valuation_daily")
        count_all = cur.fetchone()[0]

        cur.execute("""
            SELECT MIN(valuation_date), MAX(valuation_date), COUNT(DISTINCT code), COUNT(*)
            FROM valuation_daily
        """)
        summary = cur.fetchone()

        print("=" * 70)
        print("finished")
        print(f"upsert rows : {total}")
        print(f"total rows  : {count_all}")
        print(f"summary     : {summary}")
        print("=" * 70)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
