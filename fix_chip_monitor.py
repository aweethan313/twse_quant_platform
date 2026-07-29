"""
補裝籌碼監控(7/24 未成功寫入,本次重做)。
- _collect_chips 兩個失敗點登記 pending_chip
- 檔案載入時就建表(不必等第一次漏抓),巡檢查詢不再報錯
用法:python3 fix_chip_monitor.py
"""
path = 'backend/collectors/daily_eod.py'
with open(path) as f:
    c = f.read()

if '_register_pending_chip' in c:
    print("✓ 已裝,跳過")
    raise SystemExit

old1 = '''    df = twse_client.fetch_institutional(trade_date)
    if df is None or df.empty:
        logger.warning(f"[EOD] 法人資料無 {trade_date}")
        return'''
new1 = '''    df = twse_client.fetch_institutional(trade_date)
    if df is None or df.empty:
        logger.warning(f"[EOD] 法人資料無 {trade_date}")
        _register_pending_chip(db, trade_date, "chip_empty")
        return'''

old2 = '''    if not rows:
        logger.warning(f"[EOD] 法人資料解析後無可寫入資料 {trade_date}")
        return'''
new2 = '''    if not rows:
        logger.warning(f"[EOD] 法人資料解析後無可寫入資料 {trade_date}")
        _register_pending_chip(db, trade_date, "chip_parse_empty")
        return'''

helper = '''

def _register_pending_chip(db: Session, trade_date, reason: str):
    """籌碼漏抓時登記 pending_chip,供 process_pending_chip 自動補。"""
    from sqlalchemy import text as _t
    try:
        db.execute(_t("""CREATE TABLE IF NOT EXISTS pending_chip(
            trade_date TEXT PRIMARY KEY, reason TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)"""))
        db.execute(_t("INSERT OR IGNORE INTO pending_chip(trade_date, reason) VALUES(:d,:r)"),
                   {"d": str(trade_date), "r": reason})
        db.commit()
        logger.warning(f"[EOD] {trade_date} 已登記 pending_chip({reason})")
    except Exception as e:
        logger.warning(f"[EOD] pending_chip 登記失敗: {e}")
'''

r1, r2 = old1 in c, old2 in c
if not (r1 and r2):
    print(f"❌ 錨點失敗 old1={r1} old2={r2}")
    raise SystemExit

c = c.replace(old1, new1, 1).replace(old2, new2, 1) + helper
with open(path, 'w') as f:
    f.write(c)

n = c.count('pending_chip')
print(f"✓ patch 已寫入,pending_chip 出現 {n} 次(應 >= 4)")
