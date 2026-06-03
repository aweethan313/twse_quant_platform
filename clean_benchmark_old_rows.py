"""
清掉 benchmark_daily_equity 裡 2026-05-25 以前的舊列（2025 污染殘留），
然後重算策略健康分數（alpha 就會正確）。
因為 rebuild 預設已是 5/25、且只刪 >= start_date，舊列刪掉後不會再生。
idempotent。用法：python3 clean_benchmark_old_rows.py
"""
import sys, sqlite3
sys.path.insert(0, '.')

DB = 'data/db/quant.db'
CUTOFF = '2026-05-25'

# 1. 刪掉 cutoff 以前的舊列
con = sqlite3.connect(DB)
before = con.execute("SELECT COUNT(*) FROM benchmark_daily_equity WHERE benchmark_code='0050'").fetchone()[0]
con.execute("DELETE FROM benchmark_daily_equity WHERE benchmark_code='0050' AND snap_date < ?", (CUTOFF,))
con.commit()
after = con.execute("SELECT COUNT(*) FROM benchmark_daily_equity WHERE benchmark_code='0050'").fetchone()[0]
print(f"✓ 刪除 {before-after} 筆 5/25 以前的舊列（{before} → {after}）")
rng = con.execute("""SELECT MIN(snap_date), MAX(snap_date), MIN(cumulative_return), MAX(cumulative_return)
    FROM benchmark_daily_equity WHERE benchmark_code='0050'""").fetchone()
print(f"  現在範圍：{rng[0]} ~ {rng[1]}，cumulative {rng[2]:.2f}% ~ {rng[3]:.2f}%")
con.close()

# 2. 重算健康分數（這次 bench_ret 會是正確的 ~6%）
from scripts.v6_update_strategy_health_scores import update_health_scores
print("\n重算健康分數（alpha 應該會變正確）...")
update_health_scores()
