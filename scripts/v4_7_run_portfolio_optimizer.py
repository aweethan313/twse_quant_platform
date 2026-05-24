"""scripts/v4_7_run_portfolio_optimizer.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.research import run_portfolio_optimizer

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-7 Portfolio Optimizer {td} ===\n")
    plan = run_portfolio_optimizer(plan_date=td)
    print(f"總資產: {plan.get('total_capital',0):,.0f}")
    print(f"現金: {plan.get('cash',0):,.0f} ({plan.get('cash_ratio',0):.1f}%)")
    print(f"風險等級: {plan.get('risk_level')}")
    print("\n建議:")
    for s in plan.get('suggestions', []):
        print(f"  {s}")
    path = Path(f"data/reports/v4_7_portfolio_optimization_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Portfolio Optimizer {td}", ""]
    lines += [f"- {s}" for s in plan.get('suggestions', [])]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {path}")
