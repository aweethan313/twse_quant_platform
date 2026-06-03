"""
修復 benchmark +116% 問題：
  1. 把 rebuild_0050_benchmark 預設 start_date 從 2025-01-01 改成 2026-05-25
     （這樣 V5 pipeline / daily_workflow 無參數呼叫時不會再用 2025 錯價重建）
  2. 立即用 2026-05-25 重建一次乾淨的 benchmark
idempotent。用法：python3 fix_benchmark_default.py
"""
import sys
from pathlib import Path

# 1. 改預設值
bp = 'backend/v5/benchmark.py'
with open(bp) as f:
    bc = f.read()
if 'start_date: str = "2026-05-25"' in bc:
    print("✓ benchmark 預設已是 2026-05-25，跳過")
else:
    bc = bc.replace('start_date: str = "2025-01-01"', 'start_date: str = "2026-05-25"', 1)
    with open(bp, 'w') as f:
        f.write(bc)
    print("✓ benchmark 預設 start_date 改為 2026-05-25")

# 2. 立即重建乾淨 benchmark
sys.path.insert(0, '.')
from backend.v5.benchmark import rebuild_0050_benchmark
n = rebuild_0050_benchmark(start_date="2026-05-25")
print(f"✓ 已用 2026-05-25 重建 benchmark，{n} 筆")

# 驗證
import sqlite3
con = sqlite3.connect('data/db/quant.db')
rows = con.execute("""SELECT snap_date, price, cumulative_return 
    FROM benchmark_daily_equity WHERE benchmark_code='0050' 
    ORDER BY snap_date""").fetchall()
print(f"\nbenchmark 現況（{len(rows)} 筆）：")
for r in rows:
    print(f"  {r[0]}  價={r[1]}  累積={r[2]:+.2f}%")
con.close()
