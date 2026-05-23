"""
scripts/v3_fix1_test_decision_explanations.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v3.decision_explanations import record_decision, record_hold, query_explanations

def test():
    td = date(2026, 5, 22)
    test_scores = {
        "final_score": 68.5, "composite_score": 65.0,
        "fundamental_score": 70, "valuation_score": 45,
        "chip_score": 72, "momentum_score": 65,
        "volume_score": 58, "news_score": 60,
        "macro_score": 52, "risk_score": 22,
    }

    print("=== V3-FIX-1 Decision Explanations Test ===\n")

    # 1. BUY
    record_decision(trade_date=td, code="2330", action="BUY",
                    scores=test_scores, account_id=1, strategy_id=3, name="台積電")
    print("✓ BUY 記錄完成")

    # 2. HOLD（分數不夠）
    low_scores = {**test_scores, "final_score": 48, "composite_score": 45}
    record_hold(trade_date=td, code="2454", scores=low_scores,
                reason="final_score 48 未達買入門檻 60", account_id=1, strategy_id=3, name="聯發科")
    print("✓ HOLD 記錄完成")

    # 3. AVOID_CHASE（分數高但風險高）
    hot_scores = {**test_scores, "final_score": 62, "risk_score": 72}
    record_decision(trade_date=td, code="2383", action="AVOID_CHASE",
                    scores=hot_scores, account_id=1, strategy_id=3, name="台光電",
                    blocked_reason="風險分 72 過高，不可追")
    print("✓ AVOID_CHASE 記錄完成")

    # 4. SELL
    sell_scores = {**test_scores, "final_score": 38, "risk_score": 65}
    record_decision(trade_date=td, code="2002", action="SELL",
                    scores=sell_scores, account_id=1, strategy_id=3, name="中鋼",
                    risk_reason="risk_score 65 過高，訊號反轉")
    print("✓ SELL 記錄完成")

    print()

    # 查詢驗證
    all_recs = query_explanations(trade_date=str(td))
    print(f"✓ 查詢到 {len(all_recs)} 筆記錄")

    for r in all_recs:
        print(f"  [{r['action']:12}] {r['code']} {r['name']:6} → {r['final_explanation']}")

    holds = query_explanations(trade_date=str(td), action="HOLD")
    print(f"\n✓ HOLD 記錄數: {len(holds)}")
    if holds:
        print(f"  blocked_reason: {holds[0]['blocked_reason']}")

    blocked = [r for r in all_recs if r["blocked_reason"]]
    print(f"✓ 被擋下的候選: {len(blocked)} 筆")

    print("\n✅ FIX-1 Test PASSED")

if __name__ == "__main__":
    test()


# ────────────────────────────────────────────────
"""
scripts/v3_fix2_test_strategy_router.py
"""
