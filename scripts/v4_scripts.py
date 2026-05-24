"""scripts/v4_3_run_daily_workflow.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v4.daily_workflow import run_daily_workflow

def main():
    r = run_daily_workflow(date.today())
    print(f"完成 | PASS={r['pass']} WARN={r['warn']} FAIL={r['fail']} 耗時={r['duration']}秒")

if __name__ == "__main__":
    main()


# ──────────────────────────────────────────────
"""scripts/v4_9_run_strategy_kill_switch.py"""

def main_kill():
    from backend.v4.strategy_kill_switch import run_kill_switch
    from datetime import date
    from pathlib import Path

    td = date.today()
    print(f"=== V4-9 Strategy Kill Switch {td} ===\n")
    results = run_kill_switch(td)

    for r in results:
        icon = {"ACTIVE":"✅","REDUCED":"⚠️","PAUSED":"🛑","WATCHLIST":"👀"}.get(r["status"],"•")
        print(f"  {icon} S{r['strategy_id']} {r['strategy_name']:12} "
              f"狀態={r['status']:8} 倍率={r['new_weight']:.0%} "
              f"勝率={r['recent_win_rate']:.0f}% MDD={r['recent_max_drawdown']:.1f}%")
        if r["action_required"]:
            print(f"     → {r['action_required']}")

    # 報告
    path = Path(f"data/reports/v4_9_strategy_kill_switch_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 策略 Kill Switch {td}", ""]
    for r in results:
        lines.append(f"## S{r['strategy_id']} {r['strategy_name']}")
        lines.append(f"- 狀態: {r['status']}")
        lines.append(f"- 理由: {r['reason']}")
        if r["action_required"]:
            lines.append(f"- 行動: {r['action_required']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ 報告: {path}")


# ──────────────────────────────────────────────
"""scripts/v4_13_build_market_sector_classification.py"""

def main_sector():
    from backend.v4.market_sector import build_classification, get_classification
    from datetime import date
    from pathlib import Path
    import csv

    td = date.today()
    print(f"=== V4-13 股市分類系統 {td} ===\n")
    n = build_classification(td)
    print(f"✓ 分類 {n} 檔股票")

    all_cls = get_classification(limit=2000)
    # 統計各類別
    cats = {}
    for c in all_cls:
        cat = c["primary_category"]
        cats[cat] = cats.get(cat, 0) + 1
    print("\n類別分布:")
    for cat, count in sorted(cats.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat:20} {count} 檔")

    # CSV 輸出
    path_csv = Path(f"data/reports/v4_13_market_sector_classification_{td}.csv")
    path_csv.parent.mkdir(parents=True, exist_ok=True)
    if all_cls:
        with open(path_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["code","name","primary_category",
                                               "secondary_category","theme_tags_json",
                                               "risk_type","is_core_etf","theme_heat_score"])
            w.writeheader()
            w.writerows(all_cls)
    print(f"\n✓ CSV: {path_csv}")

    # 驗收幾個特定股票
    print("\n驗收:")
    for code in ["0050","2330","2324","6271","2882"]:
        cls = get_classification(code=code)
        if cls:
            c = cls[0]
            print(f"  {code} {c['name']:8} → {c['primary_category']} / {c['secondary_category']}")


if __name__ == "__main__":
    import sys
    script = os.path.basename(sys.argv[0]) if sys.argv else ""
    if "v4_3" in script:
        main()
    elif "v4_9" in script:
        main_kill()
    elif "v4_13" in script:
        main_sector()
    else:
        main()
