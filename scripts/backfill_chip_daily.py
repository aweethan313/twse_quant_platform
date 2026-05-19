"""
scripts/backfill_chip_daily.py

只補抓 chip_daily 三大法人資料。

設計重點：
1. 不重新抓日 K
2. 只根據 ohlcv_daily 已存在的交易日補籌碼
3. 每一天 commit 一次，避免中途失敗整批 rollback
4. T86 資料只使用該 trade_date 當天已公開資料，不會偷看未來
"""

import sys
import os
import argparse
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from loguru import logger

from backend.models.database import SessionLocal, init_db
from backend.collectors.daily_eod import _collect_chips


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()

    try:
        if args.end_date:
            end_date = parse_date(args.end_date)
        else:
            row = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).fetchone()
            if row is None or row[0] is None:
                raise RuntimeError("ohlcv_daily 沒有資料，請先補日 K")
            end_date = parse_date(str(row[0]))

        if args.start_date:
            start_date = parse_date(args.start_date)
        else:
            start_date = end_date - timedelta(days=365)

        date_rows = db.execute(
            text("""
                SELECT DISTINCT trade_date
                FROM ohlcv_daily
                WHERE trade_date BETWEEN :start_date AND :end_date
                ORDER BY trade_date
            """),
            {
                "start_date": start_date,
                "end_date": end_date,
            }
        ).fetchall()

        trade_dates = [parse_date(str(r[0])) for r in date_rows]

        if args.limit is not None:
            trade_dates = trade_dates[-args.limit:]

        print("=" * 60)
        print("Backfill chip_daily")
        print(f"start_date : {start_date}")
        print(f"end_date   : {end_date}")
        print(f"dates      : {len(trade_dates)}")
        print("=" * 60)

        ok = 0
        failed = 0

        for d in trade_dates:
            try:
                _collect_chips(db, d)
                db.commit()
                ok += 1
                print(f"[OK] {d}")
            except Exception as e:
                db.rollback()
                failed += 1
                logger.warning(f"[FAIL] {d}: {e}")

        print("=" * 60)
        print(f"finished. ok={ok}, failed={failed}")

        cnt = db.execute(text("SELECT COUNT(*) FROM chip_daily")).fetchone()[0]
        print(f"chip_daily count = {cnt}")
        print("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
