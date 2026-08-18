"""補齊 pending_chip:只補法人資料 + 重算該日評分(不重跑決策/成交/淨值,避免重寫歷史)。"""
import sys
from datetime import date
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.collectors.daily_eod import _collect_chips
from backend.services.latest_update import recompute_scores_for_date


def main():
    db = SessionLocal()
    db.execute(text("""CREATE TABLE IF NOT EXISTS pending_chip(
        trade_date TEXT PRIMARY KEY, reason TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)"""))
    pend = [r[0] for r in db.execute(text(
        "SELECT trade_date FROM pending_chip WHERE resolved_at IS NULL ORDER BY trade_date")).fetchall()]
    if not pend:
        print("沒有待補籌碼")
        db.close(); return

    fixed = []
    for ds in pend:
        d = date.fromisoformat(ds)
        # SPLIT_TXN:籌碼與評分拆成兩個交易
        # (uvicorn 持有 db 時,評分的大批次寫入會撞鎖,
        #  若同交易則籌碼也一併被回滾 —— 已發生三次)
        _collect_chips(db, d)
        db.commit()                      # ← 先確保籌碼落地
        n = db.execute(text("SELECT COUNT(*) FROM chip_daily WHERE trade_date=:d"), {"d": ds}).scalar() or 0
        if n > 800:
            db.execute(text("UPDATE pending_chip SET resolved_at=datetime('now','localtime') WHERE trade_date=:d"), {"d": ds})
            db.commit()
            fixed.append(ds)
            try:
                recompute_scores_for_date(d)   # 評分重算失敗不影響籌碼
                print(f"[{ds}] ✓ 籌碼 {n} 筆 + 評分重算")
            except Exception as e:
                print(f"[{ds}] ✓ 籌碼 {n} 筆(評分重算失敗,不影響資料完整性: {str(e)[:60]})")
        else:
            print(f"[{ds}] 仍抓不到({n}筆),保留 pending")
    db.close()
    print(f"完成:補齊 {len(fixed)} 天 {fixed}")


if __name__ == "__main__":
    main()
