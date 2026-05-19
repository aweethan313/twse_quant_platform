"""
scripts/recompute_latest_scores_all.py

用 ohlcv_daily 最新交易日的所有股票重算 daily_scores。

目的：
1. 讓剛補進來的 chip_daily 反映到 chip_score
2. 避免只重算 stock_universe，導致 screener 還是舊分數
3. score_date 對齊最新 trade_date，避免週末或非交易日造成日期錯位
"""

import sys
import os
from datetime import date
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.database import init_db, SessionLocal
from backend.signals.scorer import compute_scores
from backend.collectors.news_events import run_news
from scripts.update_market_context import update_context_for_date


def main():
    init_db()

    db = SessionLocal()
    try:
        latest_trade_date = db.execute(
            text("SELECT MAX(trade_date) FROM ohlcv_daily")
        ).scalar()

        if latest_trade_date is None:
            raise RuntimeError("ohlcv_daily 沒有資料，請先補日 K")

        latest_trade_date = date.fromisoformat(str(latest_trade_date))

        rows = db.execute(
            text("""
                SELECT DISTINCT code
                FROM ohlcv_daily
                WHERE trade_date = :d
                ORDER BY code
            """),
            {"d": latest_trade_date}
        ).fetchall()

        codes = [str(r[0]).strip() for r in rows if r[0]]

    finally:
        db.close()

    print("=" * 60)
    print("Recompute latest scores for all stocks")
    print(f"score_date : {latest_trade_date}")
    print(f"codes      : {len(codes)}")
    print("=" * 60)

    # 先產生結構化事件代理，避免 news_score 因 news_events 空表而固定 50。
    # run_news 只使用 latest_trade_date 當下已存在的資料，不會偷看未來。
    run_news(latest_trade_date)

    # 建立市場環境：台股廣度、成交量、開收盤結構、內外盤比、題材主線。
    # 若 data/external/overnight_market.csv 有美股 / 夜盤資料，會自動納入；沒有則外部因子中性。
    update_context_for_date(latest_trade_date, auto_external=False)

    compute_scores(codes, score_date=latest_trade_date)

    print("=" * 60)
    print("done")
    print("=" * 60)


if __name__ == "__main__":
    main()

