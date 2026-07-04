"""
歷史資料建設:用 MI_INDEX 逐日重建 2017-2023 全市場 OHLCV + trading_calendar。
- 逐日「先刪後插」:蓋掉僵屍列與幽靈日期
- 斷點續傳:hist_rebuild_progress 表記錄進度,中斷重跑會自動跳過已完成日
- --dry-run:只抓不寫,驗證資料源
用法:
  python3 -m scripts.build_history_2017_2023 --start 2017-01-01 --end 2017-01-10 --dry-run   # 冒煙測試
  python3 -m scripts.build_history_2017_2023 --start 2017-01-01 --end 2023-12-31 --sleep 3   # 正式(1.5~3hr,可中斷續跑)
"""
import argparse, sys, time
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.utils.twse_client import twse_client


def _ensure_progress_table(db):
    db.execute(text("""CREATE TABLE IF NOT EXISTS hist_rebuild_progress(
        trade_date TEXT PRIMARY KEY, status TEXT, rows INTEGER,
        fetched_at TEXT DEFAULT (datetime('now','localtime')))"""))
    db.commit()


def _done_dates(db):
    return {r[0] for r in db.execute(text(
        "SELECT trade_date FROM hist_rebuild_progress WHERE status IN ('open','closed')")).fetchall()}


def _upsert_calendar(db, d, is_open):
    db.execute(text("""INSERT INTO trading_calendar(trade_date, is_open) VALUES(:d,:o)
        ON CONFLICT(trade_date) DO UPDATE SET is_open=excluded.is_open"""),
        {"d": str(d), "o": 1 if is_open else 0})


def process_day(db, d, dry_run):
    df = twse_client.fetch_mi_index(d)
    if df is None or df.empty:
        if dry_run:
            print(f"[{d}] 休市/無資料(dry-run,不寫)")
            return "closed", 0
        db.execute(text("DELETE FROM ohlcv_daily WHERE trade_date=:d"), {"d": str(d)})
        _upsert_calendar(db, d, False)
        db.execute(text("""INSERT INTO hist_rebuild_progress(trade_date,status,rows) VALUES(:d,'closed',0)
            ON CONFLICT(trade_date) DO UPDATE SET status='closed', rows=0,
            fetched_at=datetime('now','localtime')"""), {"d": str(d)})
        db.commit()
        return "closed", 0

    if dry_run:
        print(f"[{d}] 有市,{len(df)} 檔(dry-run,不寫) 樣本: {df.iloc[0]['code']} close={df.iloc[0]['close']}")
        return "open", len(df)

    db.execute(text("DELETE FROM ohlcv_daily WHERE trade_date=:d"), {"d": str(d)})
    rows = df.to_dict("records")
    for r in rows:
        db.execute(text("""INSERT INTO ohlcv_daily
            (code, trade_date, open, high, low, close, volume, value, change, change_pct)
            VALUES (:code,:trade_date,:open,:high,:low,:close,:volume,:value,:change,:change_pct)"""),
            {k: r.get(k) for k in
             ["code","trade_date","open","high","low","close","volume","value","change","change_pct"]})
    _upsert_calendar(db, d, True)
    db.execute(text("""INSERT INTO hist_rebuild_progress(trade_date,status,rows) VALUES(:d,'open',:n)
        ON CONFLICT(trade_date) DO UPDATE SET status='open', rows=:n,
        fetched_at=datetime('now','localtime')"""), {"d": str(d), "n": len(rows)})
    db.commit()
    return "open", len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=3.0, help="每日之間額外等待秒數(防限流)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-days", type=int, default=None)
    args = ap.parse_args()

    d = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    db = SessionLocal()
    _ensure_progress_table(db)
    done = _done_dates(db) if not args.dry_run else set()

    total = open_days = 0
    t0 = time.time()
    try:
        while d <= end:
            if args.limit_days and total >= args.limit_days:
                break
            if d.weekday() >= 5:
                d += timedelta(days=1); continue
            if str(d) in done:
                d += timedelta(days=1); continue
            status, n = process_day(db, d, args.dry_run)
            total += 1
            if status == "open":
                open_days += 1
                if not args.dry_run:
                    print(f"[{d}] ✓ {n} 檔  (累計 {open_days} 個交易日, {time.time()-t0:.0f}s)")
            time.sleep(args.sleep)
            d += timedelta(days=1)
    finally:
        db.close()
    print(f"\n完成:處理 {total} 天,其中交易日 {open_days} 天,耗時 {(time.time()-t0)/60:.1f} 分鐘")


if __name__ == "__main__":
    main()
