import sqlite3
conn = sqlite3.connect('data/db/quant.db')
conn.execute("""CREATE TABLE IF NOT EXISTS pending_backfill(
  trade_date TEXT PRIMARY KEY, reason TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)""")
conn.commit(); conn.close()
print("✓ pending_backfill 表已就緒")

path = 'backend/collectors/daily_eod.py'
with open(path) as f:
    c = f.read()
if '_register_pending_backfill' in c:
    print("✓ daily_eod 已修，跳過")
    raise SystemExit

old = '''    df = twse_client.fetch_daily_all(trade_date)
    if df is None or df.empty:
        logger.warning(f"[EOD] OHLCV 無資料 {trade_date}（可能為假日）")
        return'''
new = '''    df = twse_client.fetch_daily_all(trade_date)
    if df is None or df.empty:
        logger.warning(f"[EOD] OHLCV 無資料 {trade_date}（可能為假日）")
        _register_pending_backfill(db, trade_date, "fetch_empty")
        return'''
helper = """

def _register_pending_backfill(db: Session, trade_date, reason: str):
    from sqlalchemy import text as _t
    try:
        db.execute(_t('''CREATE TABLE IF NOT EXISTS pending_backfill(
          trade_date TEXT PRIMARY KEY, reason TEXT,
          created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)'''))
        db.execute(_t(
            "INSERT OR IGNORE INTO pending_backfill(trade_date, reason) VALUES(:d,:r)"
        ), {"d": str(trade_date), "r": reason})
        db.commit()
        logger.warning(f"[EOD] {trade_date} 已登記 pending_backfill（{reason}）")
    except Exception as e:
        logger.warning(f"[EOD] pending_backfill 登記失敗: {e}")
"""
if old in c:
    c = c.replace(old, new, 1) + helper
    with open(path, 'w') as f:
        f.write(c)
    print("✓ daily_eod.py 已加入自動登記")
else:
    print("❌ 找不到錨點")
