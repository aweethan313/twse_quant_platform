"""scripts/v4_6_run_strategy_attribution.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.research import run_strategy_attribution

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-6 Strategy Attribution {td} ===\n")
    results = run_strategy_attribution(analysis_date=td)
    print(f"歸因 {len(results)} 筆")
    for r in sorted(results, key=lambda x: abs(x['pnl']), reverse=True)[:5]:
        warn = " ⚠️集中" if r['warn'] else ""
        print(f"  S{r['strategy_id']} {r['type']:6} {r['key']:10} PnL={r['pnl']:+.0f}{warn}")
    path = Path(f"data/reports/v4_6_strategy_attribution_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Strategy Attribution {td}\n\n共 {len(results)} 筆歸因\n", encoding="utf-8")
    print(f"✓ {path}")
