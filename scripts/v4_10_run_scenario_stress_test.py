"""scripts/v4_10_run_scenario_stress_test.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.research import run_scenario_stress_test, get_stress_results

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-10 Scenario Stress Test {td} ===\n")
    results = run_scenario_stress_test(test_date=td)
    for r in sorted(results, key=lambda x: x['estimated_return']):
        icon = "🔴" if r['estimated_return'] < -5 else "⚠️" if r['estimated_return'] < -2 else "🟡"
        print(f"  {icon} {r['scenario']:20} {r['estimated_return']:+.1f}%")
    path = Path(f"data/reports/v4_10_scenario_stress_test_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Scenario Stress Test {td}", ""]
    for r in get_stress_results(str(td)):
        icon = "🔴" if float(r['estimated_return'] or 0) < -5 else "⚠️"
        lines.append(f"- {icon} **{r['scenario_name']}**: {float(r['estimated_return'] or 0):+.1f}%  {r['risk_warning'] or ''}")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {path}")
