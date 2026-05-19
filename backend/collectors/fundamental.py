"""
backend/collectors/fundamental.py

Step 2：月營收資料收集器。

目前先抓「最新一期」上市 + 上櫃每月營收彙總表，
寫入 monthly_revenue。

資料來源優先順序：
1. TWSE OpenAPI JSON
2. mopsfin CSV fallback

注意：
- 這個版本重點是先讓 monthly_revenue 從 0 變成可用
- 歷史 18 個月 backfill 之後再做，避免現在卡在 MOPS 舊 NAS 404
- published_date 直接使用出表日期；如果解析不到，才用次月 15 日保守估計
"""

import os
import re
import sys
from io import StringIO, BytesIO
from datetime import date
from typing import Optional

import httpx
import pandas as pd
from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.models.database import SessionLocal, init_db, MonthlyRevenue


OPENAPI_URLS = {
    "listed": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "otc": "https://openapi.twse.com.tw/v1/opendata/t187ap05_O",
}

CSV_FALLBACK_URLS = {
    "listed": "https://mopsfin.twse.com.tw/opendata/t187ap05_L.csv",
    "otc": "https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv",
}


def _clean_number(x) -> Optional[float]:
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


def _parse_roc_or_ad_date(x) -> Optional[date]:
    if x is None:
        return None

    s = str(x).strip()
    nums = re.findall(r"\d+", s)

    if len(nums) < 3:
        return None

    y, m, d = int(nums[0]), int(nums[1]), int(nums[2])

    # 民國年
    if y < 1911:
        y += 1911

    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_data_year_month(x) -> tuple[Optional[int], Optional[int]]:
    """
    支援格式：
    - 115年04月
    - 115/04
    - 2026/04
    - 202604
    """
    if x is None:
        return None, None

    s = str(x).strip()
    nums = re.findall(r"\d+", s)

    if len(nums) >= 2:
        y = int(nums[0])
        m = int(nums[1])
    elif len(nums) == 1 and len(nums[0]) >= 5:
        raw = nums[0]
        y = int(raw[:-2])
        m = int(raw[-2:])
    else:
        return None, None

    if y < 1911:
        y += 1911

    if not (1 <= m <= 12):
        return None, None

    return y, m


def _published_date_for_month(year: int, month: int) -> date:
    # 保守估計：次月 15 日才可用，避免回測偷看未來
    if month == 12:
        return date(year + 1, 1, 15)
    return date(year, month + 1, 15)


def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = [str(c).strip() for c in df.columns]
    mapping = {str(c).strip(): c for c in df.columns}

    for target in candidates:
        if target in mapping:
            return mapping[target]

    for c in cols:
        for target in candidates:
            if target in c:
                return mapping[c]

    return None


def _normalize_monthly_revenue_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    date_col = _find_col(df, ["出表日期"])
    ym_col = _find_col(df, ["資料年月"])
    code_col = _find_col(df, ["公司代號"])
    revenue_col = _find_col(df, ["營業收入-當月營收", "當月營收"])
    mom_col = _find_col(df, ["營業收入-上月比較增減(%)", "上月比較增減"])
    yoy_col = _find_col(df, ["營業收入-去年同月增減(%)", "去年同月增減"])
    acc_col = _find_col(df, ["累計營業收入-當月累計營收", "當月累計營收"])

    required = {
        "資料年月": ym_col,
        "公司代號": code_col,
        "當月營收": revenue_col,
        "去年同月增減": yoy_col,
    }

    missing = [k for k, v in required.items() if v is None]
    if missing:
        logger.warning(f"[MONTHLY] missing columns: {missing}, columns={list(df.columns)}")
        return pd.DataFrame()

    rows = []

    for _, r in df.iterrows():
        code = str(r.get(code_col, "")).strip()

        if not re.fullmatch(r"[0-9]{4}[A-Z]?", code):
            continue

        year, month = _parse_data_year_month(r.get(ym_col))
        if year is None or month is None:
            continue

        published_date = _parse_roc_or_ad_date(r.get(date_col)) if date_col else None
        if published_date is None:
            published_date = _published_date_for_month(year, month)

        revenue = _clean_number(r.get(revenue_col))
        yoy_pct = _clean_number(r.get(yoy_col))

        if revenue is None:
            continue

        rows.append({
            "code": code,
            "year": year,
            "month": month,
            "published_date": published_date,
            "revenue": revenue,
            "mom_pct": _clean_number(r.get(mom_col)) if mom_col else None,
            "yoy_pct": yoy_pct,
            "accumulated": _clean_number(r.get(acc_col)) if acc_col else None,
        })

    out = pd.DataFrame(rows)

    if out.empty:
        return out

    out = out.drop_duplicates(subset=["code", "year", "month"], keep="last")
    return out


def _fetch_openapi(kind: str) -> Optional[pd.DataFrame]:
    url = OPENAPI_URLS[kind]
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/csv,*/*",
    }

    try:
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            r = client.get(url)

        if r.status_code != 200:
            logger.warning(f"[MONTHLY] OpenAPI {kind} status={r.status_code}")
            return None

        data = r.json()
        if not isinstance(data, list):
            logger.warning(f"[MONTHLY] OpenAPI {kind} json is not list")
            return None

        raw_df = pd.DataFrame(data)
        df = _normalize_monthly_revenue_df(raw_df)

        if df.empty:
            logger.warning(f"[MONTHLY] OpenAPI {kind} parsed empty")
            return None

        logger.info(f"[MONTHLY] OpenAPI {kind} parsed {len(df)} rows")
        return df

    except Exception as e:
        logger.warning(f"[MONTHLY] OpenAPI {kind} failed: {e}")
        return None


def _fetch_csv_fallback(kind: str) -> Optional[pd.DataFrame]:
    url = CSV_FALLBACK_URLS[kind]
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*",
    }

    try:
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            r = client.get(url)

        if r.status_code != 200:
            logger.warning(f"[MONTHLY] CSV {kind} status={r.status_code}")
            return None

        content = r.content

        # 先試 UTF-8，再試 Big5
        try:
            raw_df = pd.read_csv(BytesIO(content), encoding="utf-8-sig")
        except Exception:
            raw_df = pd.read_csv(BytesIO(content), encoding="big5", errors="ignore")

        df = _normalize_monthly_revenue_df(raw_df)

        if df.empty:
            logger.warning(f"[MONTHLY] CSV {kind} parsed empty")
            return None

        logger.info(f"[MONTHLY] CSV {kind} parsed {len(df)} rows")
        return df

    except Exception as e:
        logger.warning(f"[MONTHLY] CSV {kind} failed: {e}")
        return None


def _ensure_monthly_revenue_schema(db: Session):
    rows = db.execute(text("PRAGMA table_info(monthly_revenue)")).fetchall()
    cols = {str(r[1]) for r in rows}

    if "published_date" not in cols:
        logger.warning("[MONTHLY] add column monthly_revenue.published_date")
        db.execute(text("ALTER TABLE monthly_revenue ADD COLUMN published_date DATE"))
        db.commit()


def run_monthly_revenue(year: Optional[int] = None, month: Optional[int] = None) -> int:
    """
    抓最新一期上市 + 上櫃月營收。

    year/month 參數保留相容性，但 OpenAPI 目前抓的是最新一期資料。
    """
    if year is not None or month is not None:
        logger.warning(
            "[MONTHLY] current collector uses latest OpenAPI data; "
            "year/month arguments are ignored for now"
        )

    init_db()
    db = SessionLocal()

    try:
        _ensure_monthly_revenue_schema(db)

        frames = []

        for kind in ["listed", "otc"]:
            df = _fetch_openapi(kind)

            if df is None or df.empty:
                df = _fetch_csv_fallback(kind)

            if df is not None and not df.empty:
                frames.append(df)

        if not frames:
            logger.warning("[MONTHLY] no data fetched")
            return 0

        all_df = pd.concat(frames, ignore_index=True)
        all_df = all_df.drop_duplicates(subset=["code", "year", "month"], keep="last")

        rows = all_df.to_dict("records")

        stmt = sqlite_insert(MonthlyRevenue).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "year", "month"],
            set_={
                "published_date": stmt.excluded.published_date,
                "revenue": stmt.excluded.revenue,
                "mom_pct": stmt.excluded.mom_pct,
                "yoy_pct": stmt.excluded.yoy_pct,
                "accumulated": stmt.excluded.accumulated,
            }
        )

        db.execute(stmt)
        db.commit()

        logger.success(f"[MONTHLY] upsert {len(rows)} rows")
        return len(rows)

    finally:
        db.close()


def backfill_monthly_revenue(months: int = 18, end_year: Optional[int] = None, end_month: Optional[int] = None):
    """
    目前先抓最新一期。
    歷史多月份回補之後另做 MOPS historical collector。
    """
    logger.warning("[MONTHLY] historical backfill not implemented yet; fetching latest month only")
    n = run_monthly_revenue()
    print("=" * 60)
    print(f"monthly_revenue latest upsert rows = {n}")
    print("=" * 60)


if __name__ == "__main__":
    run_monthly_revenue()