"""
重新跑策略競賽回測 / 紙上交易模擬。V4.6

原則：
- 實際交易日 T 使用 T 之前最近 score_date 的分數。
- 不使用當日收盤後才知道的分數去買當日收盤價。
- 會先建立 start_date 前一個交易日到 end_date 的 market_context，避免第一天交易找不到前一日市場環境。
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.engine.strategy_runner import run_all_strategies, get_competition_ranking
from scripts.update_market_context import update_context_range as update_context_for_date


def _to_date(x):
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _available_trade_dates(start_date: date | None, end_date: date | None):
    db = SessionLocal()
    try:
        cond = []
        params = {}
        if start_date:
            cond.append("trade_date >= :s")
            params["s"] = start_date
        if end_date:
            cond.append("trade_date <= :e")
            params["e"] = end_date
        where = "WHERE " + " AND ".join(cond) if cond else ""
        rows = db.execute(text(f"SELECT DISTINCT trade_date FROM ohlcv_daily {where} ORDER BY trade_date"), params).fetchall()
        return [_to_date(r[0]) for r in rows]
    finally:
        db.close()


def _context_dates(start_date: date, end_date: date):
    """回測前先建立 market_context；包含 start_date 前一個交易日，避免第一天缺 context。"""
    db = SessionLocal()
    try:
        prev = db.execute(
            text("SELECT MAX(trade_date) FROM ohlcv_daily WHERE trade_date < :s"),
            {"s": start_date},
        ).scalar()
        context_start = _to_date(prev) if prev else start_date
        rows = db.execute(
            text("""
                SELECT DISTINCT trade_date FROM ohlcv_daily
                WHERE trade_date BETWEEN :s AND :e
                ORDER BY trade_date
            """),
            {"s": context_start, "e": end_date},
        ).fetchall()
        return [_to_date(r[0]) for r in rows]
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", type=str, default=None)
    p.add_argument("--end-date", type=str, default=None)
    args = p.parse_args()

    start = date.fromisoformat(args.start_date) if args.start_date else None
    end = date.fromisoformat(args.end_date) if args.end_date else None
    dates = _available_trade_dates(start, end)
    if not dates:
        print("找不到可跑的交易日")
        return

    print(f"將執行 {len(dates)} 個交易日：{dates[0]} ~ {dates[-1]}")
    print("先建立 market_context（含起始日前一個交易日；只用當日以前資料，不偷看未來）...")
    for d in _context_dates(dates[0], dates[-1]):
        update_context_for_date(start_date=str(d), end_date=str(d))

    for d in dates:
        run_all_strategies(d)

    print("\n策略排行榜：")
    ranking = get_competition_ranking(dates[0], dates[-1])
    for r in ranking:
        print(
            f"#{r['rank']} {r['name']} | {r['strategy_class']} | "
            f"return={r['return_pct']:.2f}% | maxDD={r['max_drawdown']:.2f}% | "
            f"end={r['end_equity']:,.0f}"
        )


if __name__ == "__main__":
    main()
