"""
scripts/create_overnight_market_template.py — V4.6

建立 / 補齊 data/external/overnight_market.csv。

用途：
- 歷史回測時，若你想讓「夜盤 / 美股分」不是 50，需要手動或半自動填入每個日期的外部市場資料。
- 本腳本會依 ohlcv_daily 的交易日建立空白模板，不會預設填 0，避免把「缺資料」誤判成「真的 0%」。

用法：
    python -m scripts.create_overnight_market_template --start-date 2026-04-16 --end-date 2026-05-18
    nano data/external/overnight_market.csv

CSV 欄位：
    context_date,nasdaq_ret,sox_ret,qqq_ret,sp500_ret,tw_futures_ret

填法：
    1.2   = +1.2%
    -0.8  = -0.8%
    0.012 = +1.2%
    空白  = 缺資料，系統會以 50 中性處理
"""
import argparse
import csv
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from backend.models.database import SessionLocal, init_db

FIELDS = ["context_date", "nasdaq_ret", "sox_ret", "qqq_ret", "sp500_ret", "tw_futures_ret"]
PATH = os.path.join("data", "external", "overnight_market.csv")


def _to_date(x):
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _trade_dates(start=None, end=None):
    db = SessionLocal()
    try:
        cond = []
        params = {}
        if start:
            cond.append("trade_date >= :s")
            params["s"] = start
        if end:
            cond.append("trade_date <= :e")
            params["e"] = end
        where = "WHERE " + " AND ".join(cond) if cond else ""
        rows = db.execute(text(f"SELECT DISTINCT trade_date FROM ohlcv_daily {where} ORDER BY trade_date"), params).fetchall()
        return [_to_date(r[0]).isoformat() for r in rows]
    finally:
        db.close()


def _read_existing():
    if not os.path.exists(PATH):
        return {}
    out = {}
    with open(PATH, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            d = row.get("context_date") or row.get("trade_date") or row.get("date")
            if not d:
                continue
            out[str(d)[:10]] = {k: row.get(k, "") for k in FIELDS}
            out[str(d)[:10]]["context_date"] = str(d)[:10]
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", type=str, default=None)
    p.add_argument("--end-date", type=str, default=None)
    p.add_argument("--latest-only", action="store_true", help="只建立最新交易日一列。")
    p.add_argument("--overwrite", action="store_true", help="重建檔案；不加此參數會保留舊值，只補缺少日期。")
    args = p.parse_args()

    init_db()
    os.makedirs(os.path.dirname(PATH), exist_ok=True)

    start = date.fromisoformat(args.start_date) if args.start_date else None
    end = date.fromisoformat(args.end_date) if args.end_date else None
    dates = _trade_dates(start, end)
    if args.latest_only and dates:
        dates = [dates[-1]]
    if not dates:
        print("找不到 ohlcv_daily 交易日，無法建立模板。")
        return

    existing = {} if args.overwrite else _read_existing()
    merged = dict(existing)
    for d in dates:
        merged.setdefault(d, {k: "" for k in FIELDS})
        merged[d]["context_date"] = d

    with open(PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for d in sorted(merged):
            row = {k: merged[d].get(k, "") for k in FIELDS}
            row["context_date"] = d
            writer.writerow(row)

    print(f"已建立 / 更新 {PATH}")
    print(f"日期筆數：{len(merged)}")
    print("請用 nano 填入外部市場報酬；空白代表缺資料，系統會以 50 中性處理。")


if __name__ == "__main__":
    main()
