"""scripts/v4_9_run_strategy_kill_switch.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.strategy_kill_switch import run_kill_switch

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-9 Strategy Kill Switch {td} ===\n")
    results = run_kill_switch(td)
    for r in results:
        icon = {"ACTIVE":"✅","REDUCED":"⚠️","PAUSED":"🛑","WATCHLIST":"👀"}.get(r["status"],"•")
        print(f"  {icon} S{r['strategy_id']} {r['strategy_name']:12} 狀態={r['status']:8} "
              f"倍率={r['new_weight']:.0%} 勝率={r['recent_win_rate']:.0f}% MDD={r['recent_max_drawdown']:.1f}%")
        if r.get("action_required"):
            print(f"     → {r['action_required']}")
    path = Path(f"data/reports/v4_9_strategy_kill_switch_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 策略 Kill Switch {td}", ""]
    for r in results:
        lines += [f"## S{r['strategy_id']} {r['strategy_name']}", f"- 狀態: {r['status']}",
                  f"- 理由: {r['reason']}", f"- 行動: {r.get('action_required','無')}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {path}")
