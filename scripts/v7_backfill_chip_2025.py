"""scripts/v7_backfill_chip_2025.py - 補跑 2025 全年籌碼"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from backend.utils.twse_client import TWSEClient
from backend.models.database import SessionLocal
from sqlalchemy import text

def backfill(start_date="2025-01-01", end_date=None):
    if not end_date:
        end_date = str(date.today())
    client = TWSEClient()
    db = SessionLocal()
    d = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    ok = skip = 0
    print(f"補跑籌碼 {start_date} ~ {end_date}...")
    while d <= end:
        if d.weekday() < 5:
            # 檢查是否已有非零資料
            existing = db.execute(text("""
                SELECT COUNT(*) FROM chip_daily
                WHERE trade_date=:d AND foreign_net != 0
            """), {"d": str(d)}).scalar()
            if existing and existing > 100:
                skip += 1
                d += timedelta(days=1)
                continue
            df = client.fetch_institutional(d)
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    db.execute(text("""
                        INSERT INTO chip_daily (code, trade_date, foreign_net, trust_net, dealer_net)
                        VALUES (:c, :d, :f, :t, :de)
                        ON CONFLICT(code, trade_date) DO UPDATE SET
                            foreign_net=excluded.foreign_net,
                            trust_net=excluded.trust_net,
                            dealer_net=excluded.dealer_net
                    """), {"c": row["code"], "d": str(d),
                           "f": row["foreign_net"], "t": row["trust_net"], "de": row["dealer_net"]})
                db.commit()
                ok += 1
                print(f"✓ {d} ({len(df)}股)", flush=True)
            time.sleep(0.4)
        d += timedelta(days=1)
    db.close()
    print(f"完成：補跑={ok}天 跳過={skip}天")

if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    e = sys.argv[2] if len(sys.argv) > 2 else None
    backfill(s, e)
