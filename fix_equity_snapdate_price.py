"""
修正 update_v5_equity 的 look-ahead bug:
  原本用 MAX(trade_date) 的價格算市值(=資料庫最新日),
  導致任何「回頭重算歷史淨值」都用未來價格 → 歷史 equity_curve 失真、回測完全無效。
改為:用 snap_date 當天價格;當天無報價則 fallback 到該股 <= snap_date 的最近一日。
用法:python3 fix_equity_snapdate_price.py
"""
path = 'backend/v5/paper_engine.py'
with open(path) as f:
    c = f.read()

if 'SNAPDATE_PRICE' in c:
    print("✓ 已修,跳過")
    raise SystemExit

old = '''            mkt = db.execute(text("""
                SELECT SUM(p.lots * o.close)
                FROM positions p
                LEFT JOIN ohlcv_daily o ON o.code=p.code
                    AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
                WHERE p.account_id=:id
            """), {"id": aid}).scalar() or 0'''

new = '''            # SNAPDATE_PRICE:用 snap_date 當天價格(無報價則取該股 <= snap_date 最近一日)
            mkt = db.execute(text("""
                SELECT SUM(p.lots * COALESCE(o.close, ofb.close))
                FROM positions p
                LEFT JOIN ohlcv_daily o
                       ON o.code=p.code AND o.trade_date=:d
                LEFT JOIN ohlcv_daily ofb
                       ON ofb.code=p.code
                      AND ofb.trade_date=(
                            SELECT MAX(trade_date) FROM ohlcv_daily
                            WHERE code=p.code AND trade_date <= :d)
                WHERE p.account_id=:id
            """), {"id": aid, "d": str(snap_date)}).scalar() or 0'''

if old in c:
    with open(path, 'w') as f:
        f.write(c.replace(old, new, 1))
    print("✓ update_v5_equity 已改用 snap_date 當天價格")
    print("  請執行:python3 -m py_compile backend/v5/paper_engine.py")
else:
    print("❌ 錨點失敗,請把 paper_engine.py 的市值計算段落貼給 Claude")
