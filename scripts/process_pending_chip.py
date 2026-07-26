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
        _collect_chips(db, d)
        n = db.execute(text("SELECT COUNT(*) FROM chip_daily WHERE trade_date=:d"), {"d": ds}).scalar() or 0
        if n > 800:
            recompute_scores_for_date(d)  # 籌碼是 chip_score 的輸入,補後重算評分
            db.execute(text("UPDATE pending_chip SET resolved_at=datetime('now','localtime') WHERE trade_date=:d"), {"d": ds})
            db.commit()
            fixed.append(ds)
            print(f"[{ds}] ✓ 籌碼 {n} 筆 + 評分重算")
        else:
            print(f"[{ds}] 仍抓不到({n}筆),保留 pending")
    db.close()
    print(f"完成:補齊 {len(fixed)} 天 {fixed}")


if __name__ == "__main__":
    main()
