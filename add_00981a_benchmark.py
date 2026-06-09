"""
新增 00981A「買進持有」基準線（all in 00981A，從實測起始日全壓）。
用既有 benchmark 機制（不碰 7 個選股帳戶）。
做三件事：
  1. 立即建立 00981A benchmark 資料
  2. patch reset_forward_test.py — reset 時一起重建 00981A
  3. patch scripts/daily_pipeline.py — 每日更新時一起重建 00981A
idempotent。用法：python3 add_00981a_benchmark.py
"""
import sys
sys.path.insert(0, '.')

TICKER = "00981A"

# === 1. 立即建立 00981A benchmark ===
from backend.models.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
fs = db.execute(text("SELECT MIN(start_date) FROM strategy_accounts WHERE id BETWEEN 11 AND 17")).scalar()
db.close()
fs = str(fs)[:10] if fs else "2026-05-25"

from backend.v5.benchmark import rebuild_0050_benchmark
n = rebuild_0050_benchmark(start_date=fs, benchmark_code=TICKER)
print(f"✓ 建立 {TICKER} benchmark {n} 筆（起始 {fs}）")

# === 2. patch reset_forward_test.py ===
rp = 'reset_forward_test.py'
with open(rp) as f:
    rc = f.read()
if '00981A benchmark' in rc:
    print("✓ reset_forward_test 已 patch，跳過")
else:
    anchor = 'n = rebuild_0050_benchmark(start_date=START_DATE)'
    if anchor in rc:
        add = (anchor +
               '\n        # 00981A benchmark（all in 買進持有對照）\n'
               '        rebuild_0050_benchmark(start_date=START_DATE, benchmark_code="00981A")')
        rc = rc.replace(anchor, add, 1)
        with open(rp, 'w') as f:
            f.write(rc)
        print("✓ reset_forward_test.py 已 patch（reset 時一起重建 00981A）")
    else:
        print("⚠️ reset_forward_test 找不到錨點，請手動加 rebuild_0050_benchmark(..., benchmark_code='00981A')")

# === 3. patch scripts/daily_pipeline.py ===
dp = 'scripts/daily_pipeline.py'
with open(dp) as f:
    dc = f.read()
if '00981A' in dc:
    print("✓ daily_pipeline 已 patch，跳過")
else:
    # 找含 rebuild_0050_benchmark(start_date=... 的那行（不管是 forward_start 還是寫死日期）
    lines = dc.split('\n')
    out = []
    patched = False
    for ln in lines:
        out.append(ln)
        if ('rebuild_0050_benchmark(start_date=' in ln and 'n =' in ln and not patched):
            indent = ln[:len(ln) - len(ln.lstrip())]
            arg = 'forward_start' if 'forward_start' in ln else '"2026-05-25"'
            out.append(f'{indent}# 00981A benchmark（all in 買進持有對照）')
            out.append(f'{indent}rebuild_0050_benchmark(start_date={arg}, benchmark_code="00981A")')
            patched = True
    if patched:
        with open(dp, 'w') as f:
            f.write('\n'.join(out))
        print("✓ daily_pipeline.py 已 patch（每日更新一起重建 00981A）")
    else:
        print("⚠️ daily_pipeline 找不到錨點，請手動加")

# === 驗證 ===
import sqlite3
con = sqlite3.connect('data/db/quant.db')
rows = con.execute("SELECT snap_date, cumulative_return FROM benchmark_daily_equity WHERE benchmark_code=? ORDER BY snap_date", (TICKER,)).fetchall()
print(f"\n{TICKER} 買進持有累積報酬：")
for d, c in rows:
    print(f"  {d}  {c:+.2f}%")
con.close()
print("\n後端完成。接著套用前端 patch（add_00981a_frontend.py）才會顯示在網頁。")
