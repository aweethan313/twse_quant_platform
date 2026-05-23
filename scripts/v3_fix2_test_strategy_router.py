"""scripts/v3_fix2_test_strategy_router.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v3.strategy_router import compute_router, is_strategy_enabled

def test():
    print("=== V3-FIX-2 Strategy Router Test ===\n")
    td = date(2026, 5, 22)
    result = compute_router(td)

    print(f"市場趨勢:    {result['market_trend']}")
    print(f"風險等級:    {result['risk_level']}")
    print(f"部位倍率:    {result['position_multiplier']}")
    print(f"啟用策略:    {result['enabled_strategies']}")
    print(f"停用策略:    {result['disabled_strategies']}")
    print(f"理由:        {result.get('reasons', [])}")
    print()

    for sid in [1,2,3,4,5,7]:
        enabled, pm = is_strategy_enabled(sid, td)
        print(f"  S{sid} {'✅啟用' if enabled else '❌停用'}  pm={pm}")

    print("\n✅ FIX-2 Test PASSED")

if __name__ == "__main__":
    test()
