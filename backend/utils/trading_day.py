"""backend/utils/trading_day.py

V9.1-P0：日級資料交易日守門員。

目前專案範圍是「TWSE 上市股票 + ETF、日 K 為主」，所以這裡只處理日級
pipeline 的開市日判斷，不引入分鐘資料，也不要求 TPEx。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import text
from loguru import logger

from backend.models.database import SessionLocal


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def is_weekend(value) -> bool:
    d = _to_date(value)
    return d.weekday() >= 5


def is_trading_day(value, db=None) -> bool:
    """回傳指定日期是否可跑日級資料流程。

    規則：
    1. 週六、週日一律 False。
    2. 如果 trading_calendar 有該日期且 is_open=0，False。
    3. 其他平日先視為 True；實際抓不到資料時由 collector 安全跳過。
    """
    d = _to_date(value)
    if d.weekday() >= 5:
        return False

    own_db = False
    if db is None:
        db = SessionLocal()
        own_db = True
    try:
        try:
            row = db.execute(
                text("SELECT is_open FROM trading_calendar WHERE trade_date=:d"),
                {"d": d.isoformat()},
            ).fetchone()
        except Exception:
            row = None
        if row is not None and int(row[0] or 0) == 0:
            return False
        return True
    finally:
        if own_db:
            db.close()


def latest_open_trade_date(on_or_before: Optional[date] = None) -> Optional[str]:
    """從 trading_calendar / ohlcv_daily 找最新有效交易日。"""
    cutoff = (on_or_before or date.today()).isoformat()
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT MAX(tc.trade_date)
            FROM trading_calendar tc
            WHERE tc.is_open=1 AND tc.trade_date<=:cutoff
        """), {"cutoff": cutoff}).scalar()
        if row:
            return str(row)
        row = db.execute(text("""
            SELECT MAX(trade_date)
            FROM ohlcv_daily
            WHERE trade_date<=:cutoff
              AND strftime('%w', trade_date) NOT IN ('0','6')
        """), {"cutoff": cutoff}).scalar()
        return str(row) if row else None
    finally:
        db.close()


def skip_if_not_trading_day(value, *, label: str = "daily pipeline") -> bool:
    d = _to_date(value)
    if not is_trading_day(d):
        logger.info(f"[{label}] {d} 非交易日，略過日級資料流程")
        return True
    return False
