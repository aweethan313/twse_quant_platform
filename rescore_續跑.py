from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.services.latest_update import recompute_scores_for_date

db = SessionLocal()
days = [str(r[0]) for r in db.execute(text(
    "SELECT DISTINCT trade_date FROM ohlcv_daily "
    "WHERE trade_date BETWEEN '2024-03-28' AND '2024-12-31' "
    "AND code GLOB '[0-9][0-9][0-9][0-9]' ORDER BY trade_date")).fetchall()]
db.close()
print(f"共 {len(days)} 個交易日", flush=True)
for i, d in enumerate(days, 1):
    recompute_scores_for_date(date.fromisoformat(d))
    if i % 20 == 0:
        print(f"{i}/{len(days)} {d}", flush=True)
print("完成")
