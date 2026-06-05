"""
修 daily_pipeline.py 把 benchmark 起算日寫死成 2026-06-01 的 bug，
導致每日更新時 benchmark 累積報酬從 6/01 斷裂。
改成讀「策略帳戶的實測起始日」(start_date)，與排行榜一致。
並立即用正確起始日重建一次修復現有資料。
idempotent。用法：python3 fix_pipeline_benchmark_date.py
"""
import sys, sqlite3
sys.path.insert(0, '.')

# 1. 修 daily_pipeline.py
pp = 'scripts/daily_pipeline.py'
with open(pp) as f:
    c = f.read()

if 'FORWARD_START' in c:
    print("✓ daily_pipeline 已修，跳過")
else:
    old_line = '        n = rebuild_0050_benchmark(start_date="2026-06-01")'
    new_lines = (
        '        # FORWARD_START：讀實測起始日（與排行榜一致），不可寫死\n'
        '        _db2 = SessionLocal()\n'
        '        try:\n'
        '            _fs = _db2.execute(text("SELECT MIN(start_date) FROM strategy_accounts WHERE id BETWEEN 11 AND 17")).scalar()\n'
        '        finally:\n'
        '            _db2.close()\n'
        '        forward_start = str(_fs)[:10] if _fs else "2026-05-25"\n'
        '        n = rebuild_0050_benchmark(start_date=forward_start)  # FORWARD_START'
    )
    if old_line in c:
        c = c.replace(old_line, new_lines, 1)
        with open(pp, 'w') as f:
            f.write(c)
        print("✓ daily_pipeline benchmark 改為讀實測起始日（不再寫死 6/01）")
    else:
        print("❌ 找不到目標行，請貼 daily_pipeline 136-139 行給 Claude")
        sys.exit(1)

# 2. 立即用正確起始日重建修復現有 benchmark
from backend.models.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
fs = db.execute(text("SELECT MIN(start_date) FROM strategy_accounts WHERE id BETWEEN 11 AND 17")).scalar()
db.close()
fs = str(fs)[:10] if fs else "2026-05-25"
print(f"\n實測起始日 = {fs}，重建 benchmark...")

# 先刪掉所有 benchmark 列（因為要從頭一致重算），再重建
con = sqlite3.connect('data/db/quant.db')
con.execute("DELETE FROM benchmark_daily_equity WHERE benchmark_code='0050'")
con.commit()
con.close()

from backend.v5.benchmark import rebuild_0050_benchmark
n = rebuild_0050_benchmark(start_date=fs)
print(f"✓ benchmark 重建完成 {n} 筆")

# 驗證
con = sqlite3.connect('data/db/quant.db')
rows = con.execute("SELECT snap_date, price, cumulative_return FROM benchmark_daily_equity WHERE benchmark_code='0050' ORDER BY snap_date").fetchall()
print("\n修復後 benchmark：")
for r in rows:
    print(f"  {r[0]}  價={r[1]}  累積={r[2]:+.2f}%")
con.close()
