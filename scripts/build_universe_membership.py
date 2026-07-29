"""
建立 universe_membership:每檔股票的存活區間(回測用,修 survivorship bias)。
- list_date:優先用 stock_meta.listing_date,無則用 ohlcv 首次出現日
- delist_date:用 ohlcv 最後出現日;若 >= 資料最新日的近期,視為仍在市(NULL)
- 標注 is_approx(下市日為近似值),誠實記錄局限
純讀 ohlcv_daily + stock_meta,只新增 universe_membership 表,不動任何現有資料。
用法:python3 -m scripts.build_universe_membership
"""
import sys
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal


def main():
    db = SessionLocal()
    try:
        db.execute(text("""CREATE TABLE IF NOT EXISTS universe_membership(
            code TEXT PRIMARY KEY,
            list_date TEXT,
            delist_date TEXT,
            first_seen TEXT,
            last_seen TEXT,
            active INTEGER,
            is_approx_delist INTEGER,
            note TEXT)"""))
        db.commit()

        # 資料最新日:判斷「最後出現」是否代表下市
        data_max = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()

        # 每檔的首末出現
        rows = db.execute(text("""
            SELECT code, MIN(trade_date) first_seen, MAX(trade_date) last_seen, COUNT(DISTINCT trade_date) days
            FROM ohlcv_daily GROUP BY code""")).fetchall()

        # stock_meta 的上市日與 active
        meta = {r[0]: (r[1], r[2]) for r in db.execute(text(
            "SELECT code, listing_date, is_active FROM stock_meta")).fetchall()}

        n_active = n_delist = 0
        for code, first_seen, last_seen, days in rows:
            list_dt, is_active_meta = meta.get(code, (None, None))
            list_date = str(list_dt)[:10] if list_dt else str(first_seen)

            # 判斷是否仍在市:最後出現日接近資料最新日(容忍 10 個日曆日,涵蓋停牌/假期)
            from datetime import date
            lm = date.fromisoformat(str(last_seen)[:10])
            dm = date.fromisoformat(str(data_max)[:10])
            still_trading = (dm - lm).days <= 10

            if still_trading:
                delist_date = None
                active = 1
                is_approx = 0
                note = "in_market"
                n_active += 1
            else:
                delist_date = str(last_seen)[:10]   # 近似:最後出現日
                active = 0
                is_approx = 1
                note = "delist_approx_by_last_seen"
                n_delist += 1

            db.execute(text("""
                INSERT INTO universe_membership(code, list_date, delist_date, first_seen, last_seen, active, is_approx_delist, note)
                VALUES(:c,:ld,:dd,:fs,:ls,:a,:ap,:nt)
                ON CONFLICT(code) DO UPDATE SET
                  list_date=excluded.list_date, delist_date=excluded.delist_date,
                  first_seen=excluded.first_seen, last_seen=excluded.last_seen,
                  active=excluded.active, is_approx_delist=excluded.is_approx_delist, note=excluded.note
            """), {"c": code, "ld": list_date, "dd": delist_date,
                   "fs": str(first_seen)[:10], "ls": str(last_seen)[:10],
                   "a": active, "ap": is_approx, "nt": note})

        db.commit()
        total = n_active + n_delist
        print(f"✓ universe_membership 建立完成:{total} 檔")
        print(f"  在市:{n_active} 檔 / 已下市(近似):{n_delist} 檔")
        print(f"  資料最新日={data_max}(用於判斷是否仍在市)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
