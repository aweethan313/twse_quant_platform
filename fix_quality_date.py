"""
修 /api/quality/data 預設查「今天」導致收盤前報錯（Expecting value）。
改成預設查「ohlcv_daily 最新有資料的交易日」，並把舊的髒報告清掉重算。
idempotent。用法：python3 fix_quality_date.py
"""
import sys, sqlite3
sys.path.insert(0, '.')

# 1. 改 main.py：預設日期用最新交易日
mp = 'main.py'
with open(mp) as f:
    c = f.read()

old = '''def api_data_quality(query_date: str = None, limit: int = 50):
    """V4-1 資料品質檢查"""
    from backend.v4.data_quality import run_data_quality_checks, get_quality_report
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()'''
new = '''def api_data_quality(query_date: str = None, limit: int = 50):
    """V4-1 資料品質檢查"""
    from backend.v4.data_quality import run_data_quality_checks, get_quality_report
    from backend.models.database import SessionLocal as _SL
    from sqlalchemy import text as _t
    from datetime import date as ddate
    if query_date:
        td = ddate.fromisoformat(query_date)
    else:
        # 預設查「最新有資料的交易日」（避免收盤前查今天沒資料而報錯）
        _db = _SL()
        try:
            _latest = _db.execute(_t("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()
        finally:
            _db.close()
        td = ddate.fromisoformat(str(_latest)) if _latest else ddate.today()'''

if '最新有資料的交易日' in c:
    print("✓ main.py 已修，跳過")
elif old in c:
    c = c.replace(old, new)
    with open(mp, 'w') as f:
        f.write(c)
    print("✓ /api/quality/data 改為預設查最新交易日")
else:
    print("❌ 找不到目標，請貼 1362 行附近給 Claude")

# 2. 清掉舊的髒報告（讓它重算）
DB = 'data/db/quant.db'
con = sqlite3.connect(DB)
try:
    n = con.execute("SELECT COUNT(*) FROM data_quality_checks WHERE check_date < '2026-05-25'").fetchone()[0]
    con.execute("DELETE FROM data_quality_checks WHERE check_date < '2026-05-25'")
    con.commit()
    print(f"✓ 清掉 {n} 筆 5/25 以前的舊品質報告")
except Exception as e:
    print(f"  （清理舊報告略過：{e}）")
con.close()

# 3. 重算 6/2 的品質檢查
from backend.v4.data_quality import run_data_quality_checks
from datetime import date
r = run_data_quality_checks(date(2026, 6, 2))
print(f"✓ 6/2 重算：健康分={r.get('overall_health')} PASS={r.get('pass')} WARN={r.get('warn')} FAIL={r.get('fail')}")
