"""
scripts/backfill_monthly_revenue_history.py

補抓歷史 monthly_revenue 月營收資料。

資料來源：
- MOPS NAS 歷史月營收檔
- 上市 sii / 上櫃 otc
- 本國公司 _0 + KY / 外國註冊公司 _1

No-lookahead 設計：
- published_date 使用「次月 15 日」保守估計
- scorer 必須用 published_date <= score_date
"""

import os
import re
import sys
import time
import argparse
import sqlite3
from io import StringIO
from datetime import date
from typing import Optional

import httpx
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings


def clean_number(x) -> Optional[float]:
    if x is None:
        return None

    s = str(x).strip()
    s = s.replace(",", "")
    s = s.replace("%", "")
    s = s.replace("＋", "+")
    s = s.replace("－", "-")
    s = s.replace("--", "")
    s = s.replace("—", "")
    s = s.replace("nan", "")
    s = s.replace("NaN", "")

    if s in ("", "-", "不適用", "不適用。", "N/A", "NA", "None"):
        return None

    try:
        return float(s)
    except ValueError:
        return None


def published_date_for_month(year: int, month: int) -> date:
    """
    月營收通常在次月公告。
    為了避免回測偷看未來，先保守使用次月 15 日作為可用日期。
    """
    if month == 12:
        return date(year + 1, 1, 15)
    return date(year, month + 1, 15)


def previous_month(today: Optional[date] = None) -> tuple[int, int]:
    if today is None:
        today = date.today()

    if today.month == 1:
        return today.year - 1, 12

    return today.year, today.month - 1


def month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def candidate_urls(year: int, month: int) -> list[str]:
    """
    MOPS 歷史月營收常見格式：

    上市：
    https://mops.twse.com.tw/nas/t21/sii/t21sc03_115_4_0.html

    上櫃：
    https://mops.twse.com.tw/nas/t21/otc/t21sc03_115_4_0.html

    最後的：
    - _0：本國公司
    - _1：KY / 外國註冊公司
    """
    roc_year = year - 1911

    urls = []
    bases = [
        "https://mops.twse.com.tw/nas/t21",
        "https://mopsov.twse.com.tw/nas/t21",
    ]

    for base in bases:
        for market in ["sii", "otc"]:
            for company_group in [0, 1]:
                for ext in ["html", "htm"]:
                    urls.append(
                        f"{base}/{market}/t21sc03_{roc_year}_{month}_{company_group}.{ext}"
                    )

    return urls


def parse_tables_from_html(html: str, year: int, month: int) -> pd.DataFrame:
    try:
        tables = pd.read_html(StringIO(html))
    except Exception:
        return pd.DataFrame()

    records = []

    for table in tables:
        if table is None or table.empty:
            continue

        # 月營收表通常是 10~11 欄。
        # 太短的表通常是標題或說明表，跳過。
        if table.shape[1] < 8:
            continue

        for _, row in table.iterrows():
            values = [str(x).strip() for x in row.tolist()]

            code_idx = None
            for i, v in enumerate(values[:4]):
                if re.fullmatch(r"[0-9]{4}[A-Z]?", v):
                    code_idx = i
                    break

            if code_idx is None:
                continue

            if len(values) <= code_idx + 7:
                continue

            code = values[code_idx]

            # 常見欄位順序：
            # 公司代號, 公司名稱, 當月營收, 上月營收, 去年當月營收,
            # 上月比較增減(%), 去年同月增減(%), 當月累計營收, 去年累計營收,
            # 前期比較增減(%), 備註
            revenue = clean_number(values[code_idx + 2])
            mom_pct = clean_number(values[code_idx + 5])
            yoy_pct = clean_number(values[code_idx + 6])
            accumulated = clean_number(values[code_idx + 7])

            if revenue is None:
                continue

            records.append({
                "code": code,
                "year": year,
                "month": month,
                "published_date": published_date_for_month(year, month).isoformat(),
                "revenue": revenue,
                "mom_pct": mom_pct,
                "yoy_pct": yoy_pct,
                "accumulated": accumulated,
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.drop_duplicates(subset=["code", "year", "month"], keep="last")
    return df


def fetch_monthly_revenue_one_month(year: int, month: int, delay_sec: float = 1.0) -> pd.DataFrame:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TWSEQuantResearch/1.0)",
    }

    frames = []

    for url in candidate_urls(year, month):
        time.sleep(delay_sec)

        try:
            with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
                r = client.get(url)

            if r.status_code != 200:
                print(f"[SKIP] {year}-{month:02d} status={r.status_code} {url}")
                continue

            html = r.content.decode("big5", errors="ignore")

            if len(html.strip()) < 500 or "404" in html.lower() or "查無資料" in html:
                print(f"[SKIP] {year}-{month:02d} empty {url}")
                continue

            df = parse_tables_from_html(html, year, month)

            if df.empty:
                print(f"[SKIP] {year}-{month:02d} parsed empty {url}")
                continue

            print(f"[OK] {year}-{month:02d} rows={len(df):4d} {url}")
            frames.append(df)

        except Exception as e:
            print(f"[FAIL] {year}-{month:02d} {url} | {e}")

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["code", "year", "month"], keep="last")
    return out


def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(monthly_revenue)")
    cols = {row[1] for row in cur.fetchall()}

    if "published_date" not in cols:
        print("[SCHEMA] add monthly_revenue.published_date")
        cur.execute("ALTER TABLE monthly_revenue ADD COLUMN published_date DATE")
        conn.commit()


def upsert_monthly_revenue(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0

    sql = """
        INSERT INTO monthly_revenue
            (code, year, month, published_date, revenue, mom_pct, yoy_pct, accumulated)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, year, month)
        DO UPDATE SET
            published_date = excluded.published_date,
            revenue = excluded.revenue,
            mom_pct = excluded.mom_pct,
            yoy_pct = excluded.yoy_pct,
            accumulated = excluded.accumulated
    """

    rows = []
    for _, r in df.iterrows():
        rows.append((
            str(r["code"]),
            int(r["year"]),
            int(r["month"]),
            str(r["published_date"]),
            None if pd.isna(r["revenue"]) else float(r["revenue"]),
            None if pd.isna(r["mom_pct"]) else float(r["mom_pct"]),
            None if pd.isna(r["yoy_pct"]) else float(r["yoy_pct"]),
            None if pd.isna(r["accumulated"]) else float(r["accumulated"]),
        ))

    conn.executemany(sql, rows)
    conn.commit()

    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--end-month", type=int, default=None)
    parser.add_argument("--delay-sec", type=float, default=1.0)
    args = parser.parse_args()

    if args.end_year is None or args.end_month is None:
        end_year, end_month = previous_month()
    else:
        end_year, end_month = args.end_year, args.end_month

    start_year, start_month = month_add(end_year, end_month, -(args.months - 1))

    print("=" * 70)
    print("Backfill historical monthly_revenue")
    print(f"DB      : {settings.DB_PATH}")
    print(f"start   : {start_year}-{start_month:02d}")
    print(f"end     : {end_year}-{end_month:02d}")
    print(f"months  : {args.months}")
    print("=" * 70)

    conn = sqlite3.connect(settings.DB_PATH)
    ensure_schema(conn)

    total_rows = 0
    ok_months = 0
    empty_months = 0

    try:
        for i in range(args.months):
            y, m = month_add(start_year, start_month, i)
            df = fetch_monthly_revenue_one_month(y, m, delay_sec=args.delay_sec)

            if df.empty:
                empty_months += 1
                print(f"[MONTH EMPTY] {y}-{m:02d}")
                continue

            n = upsert_monthly_revenue(conn, df)
            total_rows += n
            ok_months += 1
            print(f"[MONTH DONE ] {y}-{m:02d} upsert={n}")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM monthly_revenue")
        count_all = cur.fetchone()[0]

        cur.execute("""
            SELECT MIN(year || '-' || printf('%02d', month)),
                   MAX(year || '-' || printf('%02d', month)),
                   COUNT(DISTINCT code),
                   COUNT(*)
            FROM monthly_revenue
        """)
        summary = cur.fetchone()

        print("=" * 70)
        print("finished")
        print(f"ok_months    : {ok_months}")
        print(f"empty_months : {empty_months}")
        print(f"upsert_rows  : {total_rows}")
        print(f"total_rows   : {count_all}")
        print(f"range        : {summary}")
        print("=" * 70)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
