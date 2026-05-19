"""
scripts/backfill_fundamental_profitability.py

補 fundamental 表的獲利能力資料。

資料來源：
- MOPS 綜合損益表 ajax_t163sb04
- 上市 sii
- 上櫃 otc

目前補：
- eps
- gross_margin
- op_margin
- net_margin

No-lookahead：
- 寫入 published_date
- scorer 之後只使用 published_date <= score_date
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


MOPS_URL = "https://mops.twse.com.tw/mops/web/ajax_t163sb04"


def clean_number(x) -> Optional[float]:
    if x is None:
        return None

    s = str(x).strip()
    s = s.replace(",", "")
    s = s.replace("%", "")
    s = s.replace("％", "")
    s = s.replace("＋", "+")
    s = s.replace("－", "-")
    s = s.replace("--", "")
    s = s.replace("—", "")
    s = s.replace("nan", "")
    s = s.replace("NaN", "")
    s = s.replace("(", "-").replace(")", "")

    if s in ("", "-", "不適用", "N/A", "NA", "None", "null"):
        return None

    try:
        return float(s)
    except ValueError:
        return None


def published_date_for_quarter(year: int, quarter: int) -> str:
    """
    保守可用日期，避免回測偷看未來。
    Q1：5/15
    Q2：8/15
    Q3：11/15
    Q4：隔年 3/31
    """
    if quarter == 1:
        return date(year, 5, 15).isoformat()
    if quarter == 2:
        return date(year, 8, 15).isoformat()
    if quarter == 3:
        return date(year, 11, 15).isoformat()
    return date(year + 1, 3, 31).isoformat()


def find_col(df: pd.DataFrame, keyword_groups) -> Optional[str]:
    """
    keyword_groups:
    [
      ["營業收入"],
      ["基本每股盈餘"],
    ]
    """
    cols = [str(c).strip() for c in df.columns]
    mapping = {str(c).strip(): c for c in df.columns}

    for keywords in keyword_groups:
        for col_name in cols:
            if all(k in col_name for k in keywords):
                return mapping[col_name]

    return None


def normalize_table(table: pd.DataFrame, year: int, quarter: int, market: str) -> pd.DataFrame:
    if table is None or table.empty:
        return pd.DataFrame()

    df = table.copy()

    # pandas.read_html 有時會抓到 MultiIndex 欄位，先攤平成字串
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join([str(x).strip() for x in col if str(x).strip() and str(x) != "nan"])
            for col in df.columns
        ]
    else:
        df.columns = [str(c).strip() for c in df.columns]

    code_col = find_col(df, [["公司", "代號"], ["代號"]])
    revenue_col = find_col(df, [["營業收入"], ["收入"]])
    gross_col = find_col(df, [["營業毛利"], ["毛利"]])
    op_income_col = find_col(df, [["營業利益"], ["營業淨利"]])
    net_income_col = find_col(df, [["本期淨利"], ["稅後淨利"], ["淨利"]])
    eps_col = find_col(df, [["基本每股盈餘"], ["每股盈餘"], ["EPS"]])

    if code_col is None:
        return pd.DataFrame()

    rows = []

    for _, r in df.iterrows():
        code = str(r.get(code_col, "")).strip()

        if not re.fullmatch(r"[0-9]{4}[A-Z]?", code):
            continue

        revenue = clean_number(r.get(revenue_col)) if revenue_col else None
        gross_profit = clean_number(r.get(gross_col)) if gross_col else None
        op_income = clean_number(r.get(op_income_col)) if op_income_col else None
        net_income = clean_number(r.get(net_income_col)) if net_income_col else None
        eps = clean_number(r.get(eps_col)) if eps_col else None

        gross_margin = None
        op_margin = None
        net_margin = None

        if revenue is not None and revenue != 0:
            if gross_profit is not None:
                gross_margin = gross_profit / revenue * 100.0
            if op_income is not None:
                op_margin = op_income / revenue * 100.0
            if net_income is not None:
                net_margin = net_income / revenue * 100.0

        # 至少要有一個可用欄位，不然不要寫入
        if eps is None and gross_margin is None and op_margin is None and net_margin is None:
            continue

        rows.append({
            "code": code,
            "year": year,
            "quarter": quarter,
            "published_date": published_date_for_quarter(year, quarter),
            "eps": eps,
            "gross_margin": gross_margin,
            "op_margin": op_margin,
            "net_margin": net_margin,
            "market": market,
        })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out = out.drop_duplicates(subset=["code", "year", "quarter"], keep="last")
    return out


def fetch_mops_income_statement(year: int, quarter: int, market: str) -> pd.DataFrame:
    """
    market:
    - sii：上市
    - otc：上櫃

    MOPS 使用民國年。
    """
    roc_year = year - 1911

    params = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "TYPEK": market,
        "year": str(roc_year),
        "season": f"{quarter:02d}",
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://mops.twse.com.tw/mops/web/t163sb04",
    }

    try:
        with httpx.Client(timeout=60, headers=headers, follow_redirects=True) as client:
            r = client.get(MOPS_URL, params=params)

        if r.status_code != 200:
            print(f"[SKIP] {market} {year}Q{quarter} status={r.status_code}")
            return pd.DataFrame()

        html = r.content.decode("utf-8", errors="ignore")

        if "查無資料" in html or "無符合條件" in html or len(html.strip()) < 500:
            print(f"[SKIP] {market} {year}Q{quarter} empty")
            return pd.DataFrame()

        tables = pd.read_html(StringIO(html))

    except Exception as e:
        print(f"[FAIL] {market} {year}Q{quarter}: {e}")
        return pd.DataFrame()

    frames = []

    for table in tables:
        df = normalize_table(table, year, quarter, market)

        if not df.empty:
            frames.append(df)

    if not frames:
        print(f"[SKIP] {market} {year}Q{quarter} parsed empty")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["code", "year", "quarter"], keep="last")

    print(f"[OK] {market} {year}Q{quarter} rows={len(out)}")
    return out


def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(fundamental)")
    cols = {row[1] for row in cur.fetchall()}

    if "published_date" not in cols:
        print("[SCHEMA] add fundamental.published_date")
        cur.execute("ALTER TABLE fundamental ADD COLUMN published_date DATE")
        conn.commit()


def upsert_fundamental(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0

    sql = """
        INSERT INTO fundamental
            (code, year, quarter, published_date, eps, gross_margin, op_margin, net_margin)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, year, quarter)
        DO UPDATE SET
            published_date = excluded.published_date,
            eps = COALESCE(excluded.eps, fundamental.eps),
            gross_margin = COALESCE(excluded.gross_margin, fundamental.gross_margin),
            op_margin = COALESCE(excluded.op_margin, fundamental.op_margin),
            net_margin = COALESCE(excluded.net_margin, fundamental.net_margin)
    """

    rows = []

    for _, r in df.iterrows():
        rows.append((
            str(r["code"]),
            int(r["year"]),
            int(r["quarter"]),
            str(r["published_date"]),
            None if pd.isna(r["eps"]) else float(r["eps"]),
            None if pd.isna(r["gross_margin"]) else float(r["gross_margin"]),
            None if pd.isna(r["op_margin"]) else float(r["op_margin"]),
            None if pd.isna(r["net_margin"]) else float(r["net_margin"]),
        ))

    conn.executemany(sql, rows)
    conn.commit()

    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--quarter", type=int, default=1)
    parser.add_argument("--quarters", type=int, default=1)
    parser.add_argument("--delay-sec", type=float, default=1.0)
    args = parser.parse_args()

    conn = sqlite3.connect(settings.DB_PATH)
    ensure_schema(conn)

    total = 0

    try:
        # 往回補 args.quarters 季
        y = args.year
        q = args.quarter

        targets = []

        for _ in range(args.quarters):
            targets.append((y, q))
            q -= 1
            if q == 0:
                y -= 1
                q = 4

        targets.reverse()

        print("=" * 70)
        print("Backfill fundamental profitability")
        print(f"DB      : {settings.DB_PATH}")
        print(f"targets : {targets}")
        print("=" * 70)

        for year, quarter in targets:
            frames = []

            for market in ["sii", "otc"]:
                df = fetch_mops_income_statement(year, quarter, market)

                if not df.empty:
                    frames.append(df)

                time.sleep(args.delay_sec)

            if not frames:
                print(f"[QUARTER EMPTY] {year}Q{quarter}")
                continue

            all_df = pd.concat(frames, ignore_index=True)
            all_df = all_df.drop_duplicates(subset=["code", "year", "quarter"], keep="last")

            n = upsert_fundamental(conn, all_df)
            total += n

            print(f"[QUARTER DONE ] {year}Q{quarter} upsert={n}")

        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM fundamental")
        count_all = cur.fetchone()[0]

        cur.execute("""
            SELECT MIN(year || 'Q' || quarter),
                   MAX(year || 'Q' || quarter),
                   COUNT(DISTINCT code),
                   COUNT(*)
            FROM fundamental
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