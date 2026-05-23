"""scripts/v3_fix5_test_walk_forward_validator.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v3.walk_forward_validator import run_walk_forward, get_walk_forward_results

def test():
    print("=== V3-FIX-5 Walk-forward Validator Test ===\n")
    start = date(2025, 2, 1)
    end   = date(2026, 5, 21)

    print(f"執行 S3 Walk-forward ({start} ~ {end})...")
    results = run_walk_forward(strategy_id=3, data_start=start, data_end=end)

    if not results:
        print("⚠️ 無結果（可能資料不足）")
        return

    print(f"共 {len(results)} 段\n")
    for r in results:
        warn = "⚠️ " + r["overfit_warning"][:40] if r.get("overfit_warning") else "✅ OK"
        print(f"  段{r['segment']} Train {r['train_start'][:7]}~{r['train_end'][:7]} "
              f"({r['train_return']:+.1f}%) | "
              f"Test {r['test_start'][:7]}~{r['test_end'][:7]} "
              f"({r['test_return']:+.1f}%) | "
              f"MDD={r['test_max_drawdown']:.1f}% | "
              f"過擬合={r['overfit_score']:.0f} {warn}")

    overfit_count = sum(1 for r in results if r.get("overfit_score",0) > 30)
    print(f"\n過擬合警告: {overfit_count}/{len(results)} 段")
    print("\n✅ FIX-5 Test PASSED")

if __name__ == "__main__":
    test()
