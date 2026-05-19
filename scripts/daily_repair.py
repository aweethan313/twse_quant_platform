"""每天自動修復 50 檔壞資料，約 10 分鐘跑完"""
import json
from pathlib import Path
from backend.models.database import SessionLocal, OHLCVDaily
from backend.utils.twse_client import twse_client
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

PROGRESS_FILE = Path("data/repair_progress.json")
MONTHS = [(2025,m) for m in range(5,13)] + [(2026,m) for m in range(1,6)]
BATCH = 50

def get_stale_codes():
    db = SessionLocal()
    rows = db.execute(text('''
        SELECT code FROM (
            SELECT code FROM ohlcv_daily
            WHERE trade_date BETWEEN "2025-05-01" AND "2026-05-15"
            GROUP BY code, open, high, low, close
            HAVING COUNT(*) >= 5
        ) GROUP BY code
    ''')).fetchall()
    db.close()
    common = [r[0] for r in rows if len(r[0])==4 and r[0].isdigit() and not r[0].startswith('00')]
    return common

def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"done": []}

def save_progress(done):
    PROGRESS_FILE.parent.mkdir(exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps({"done": done}))

def repair(codes):
    db = SessionLocal()
    ok = 0
    for code in codes:
        for year, month in MONTHS:
            try:
                df = twse_client.fetch_stock_month(code, year, month)
                if df is None or df.empty: continue
                rows = [{'code':code,'trade_date':r['trade_date'],
                    'open':r.get('open'),'high':r.get('high'),
                    'low':r.get('low'),'close':r.get('close'),
                    'volume':r.get('volume'),'change':r.get('change')}
                    for _,r in df.iterrows()]
                if rows:
                    stmt = sqlite_insert(OHLCVDaily).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['code','trade_date'],
                        set_={'open':stmt.excluded.open,'high':stmt.excluded.high,
                              'low':stmt.excluded.low,'close':stmt.excluded.close,
                              'volume':stmt.excluded.volume,'change':stmt.excluded.change})
                    db.execute(stmt)
            except: pass
        db.commit()
        ok += 1
        print(f"  修復 {code} ({ok}/{len(codes)})")
    db.close()
    return ok

if __name__ == "__main__":
    progress = load_progress()
    done = set(progress["done"])
    all_stale = get_stale_codes()
    remaining = [c for c in all_stale if c not in done]
    print(f"待修：{len(remaining)} 檔，今日修 {BATCH} 檔")
    batch = remaining[:BATCH]
    if not batch:
        print("全部修完了！")
    else:
        repair(batch)
        done.update(batch)
        save_progress(list(done))
        print(f"今日完成，累計已修：{len(done)} 檔，剩餘：{len(remaining)-len(batch)} 檔")
