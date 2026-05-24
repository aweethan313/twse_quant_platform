"""scripts/v4_5_analyze_backtest_paper_gap.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.research import analyze_backtest_paper_gap, get_gap_analysis

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-5 Backtest vs Paper Gap {td} ===\n")
    results = analyze_backtest_paper_gap(analysis_date=td)
    print(f"分析 {len(results)} 筆")
    for r in results[:5]:
        print(f"  {r['code']} gap={r['fill_price_gap']:+.2f} 原因={r['gap_reason']}")
    path = Path(f"data/reports/v4_5_backtest_paper_gap_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Backtest vs Paper Gap {td}", ""]
    for r in get_gap_analysis(limit=50):
        lines.append(f"- {r['code']} {r['gap_reason']} ({r['severity']})")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {path}")
