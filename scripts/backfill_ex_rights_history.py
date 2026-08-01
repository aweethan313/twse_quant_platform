"""
回補歷史除權息 2017-2025 到 corporate_actions(回測地基 2/2)。
用現成 twse_client.fetch_ex_rights 逐年抓(TWT48U 支援歷史區間)。
冪等:重跑會 upsert 更新,不重複。
用法:python3 -m scripts.backfill_ex_rights_history
      python3 -m scripts.backfill_ex_rights_history --start-year 2020 --end-year 2022
"""
import sys, time, argparse
from datetime import date
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.utils.twse_client import twse_client


def backfill_year(db, year: int) -> int:
    """逐季抓(TWT48U 單次區間別太大,以季為單位較穩)"""
    total = 0
    quarters = [
        (date(year, 1, 1), date(year, 3, 31)),
        (date(year, 4, 1), date(year, 6, 30)),
        (date(year, 7, 1), date(year, 9, 30)),
        (date(year, 10, 1), date(year, 12, 31)),
    ]
    for start, end in quarters:
        if start > date.today():
            break
        try:
            df = twse_client.fetch_ex_rights(start, end)
        except Exception as e:
            print(f"  [{start}~{end}] 抓取失敗: {e}")
            time.sleep(2)
            continue
        if df is None or df.empty:
            time.sleep(1.5)
            continue
        for _, r in df.iterrows():
            db.execute(text("""
                INSERT INTO corporate_actions(code, ex_date, action_type, cash_dividend, stock_dividend_ratio)
                VALUES(:c,:d,:t,:cd,:sr)
                ON CONFLICT(code, ex_date) DO UPDATE SET
                  action_type=excluded.action_type,
                  cash_dividend=COALESCE(excluded.cash_dividend, corporate_actions.cash_dividend),
                  stock_dividend_ratio=excluded.stock_dividend_ratio,
                  fetched_at=datetime('now','localtime')
            """), {"c": r["code"], "d": r["ex_date"], "t": r["action_type"],
                   "cd": r["cash_dividend"], "sr": r["stock_dividend_ratio"]})
            total += 1
        db.commit()
        print(f"  [{start}~{end}] {len(df)} 筆")
        time.sleep(1.5)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2017)
    ap.add_argument("--end-year", type=int, default=2025)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        grand = 0
        for yr in range(args.start_year, args.end_year + 1):
            print(f"=== {yr} ===")
            grand += backfill_year(db, yr)
        # 統計
        rng = db.execute(text("SELECT MIN(ex_date), MAX(ex_date), COUNT(*) FROM corporate_actions")).fetchone()
        print(f"\n✓ 完成,本次處理 {grand} 筆")
        print(f"  corporate_actions 現況:{rng[2]} 筆,範圍 {rng[0]} ~ {rng[1]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
