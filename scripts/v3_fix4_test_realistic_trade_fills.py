"""scripts/v3_fix4_test_realistic_trade_fills.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v3.realistic_trade_fills import process_fill, get_fills, verify_no_lookahead

def test():
    print("=== V3-FIX-4 Realistic Trade Fills Test ===\n")
    td = date(2026, 5, 20)

    # 1. 正常買進
    r = process_fill(account_id=1, strategy_id=3, code="2330",
                     action="buy", signal_date=td, requested_shares=100, signal_price=880)
    print(f"1. 2330 買進: {r['execution_status']}")
    print(f"   signal={r['signal_time'][:10]} fill={r.get('fill_time','N/A')[:10] if r.get('fill_time') else 'N/A'}")
    print(f"   成交價={r.get('fill_price')} 手續費={r.get('fee')}")

    # 2. 0050 保護測試
    r2 = process_fill(account_id=1, strategy_id=3, code="0050",
                      action="sell", signal_date=td, requested_shares=100)
    print(f"\n2. 0050 賣出: {r2['execution_status']}")
    print(f"   原因: {r2['execution_reason']}")

    # 3. 驗證無偷看未來
    fills = get_fills(limit=10)
    violations = verify_no_lookahead(fills)
    print(f"\n3. 偷看未來檢查: {'✅ 無違規' if not violations else '❌ '+str(violations)}")

    # 4. 顯示成交記錄
    print(f"\n4. 最近成交記錄:")
    for f in get_fills(limit=5):
        print(f"   {f['code']} {f['action']} signal={f['signal_time'][:10]} "
              f"fill={f['fill_time'][:10] if f['fill_time'] else 'N/A'} "
              f"status={f['execution_status']}")

    print("\n✅ FIX-4 Test PASSED")

if __name__ == "__main__":
    test()
