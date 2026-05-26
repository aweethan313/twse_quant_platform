"""scripts/audit_no_lookahead.py
檢查所有資料是否有偷看未來的問題
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.models.database import SessionLocal
from sqlalchemy import text

def run():
    db = SessionLocal()
    issues = []
    warns = []

    print("=== No-Lookahead Audit ===\n")

    # 1. daily_scores score_date <= 使用當日
    r = db.execute(text("""
        SELECT COUNT(*) FROM strategy_decision_logs sdl
        JOIN daily_scores ds ON ds.code=sdl.code
        WHERE ds.score_date > sdl.signal_date
    """)).scalar()
    if r:
        issues.append(f"❌ {r} 筆 decision 使用了未來的 daily_scores")
    else:
        print("✅ daily_scores 無偷看未來")

    # 2. technical_features date <= signal_date
    r2 = db.execute(text("""
        SELECT COUNT(*) FROM strategy_decision_logs sdl
        JOIN technical_daily_features tdf ON tdf.code=sdl.code
        WHERE tdf.trade_date > sdl.signal_date
    """)).scalar()
    if r2:
        issues.append(f"❌ {r2} 筆 decision 使用了未來的 technical_features")
    else:
        print("✅ technical_daily_features 無偷看未來")

    # 3. signal_date < execution_date
    r3 = db.execute(text("""
        SELECT COUNT(*) FROM strategy_decision_logs
        WHERE execution_date IS NOT NULL AND signal_date >= execution_date
    """)).scalar()
    if r3:
        issues.append(f"❌ {r3} 筆 decision signal_date >= execution_date（應該 signal < execution）")
    else:
        print("✅ signal_date < execution_date")

    # 4. paper_fills fill_time >= signal_date
    r4 = db.execute(text("""
        SELECT COUNT(*) FROM paper_fills
        WHERE fill_time IS NOT NULL AND date(fill_time) <= signal_date
    """)).scalar()
    if r4:
        warns.append(f"⚠️ {r4} 筆 fill_time <= signal_date（可能當日成交）")
    else:
        print("✅ paper_fills fill_time 正常")

    # 5. 0050 不被短線賣出
    r5 = db.execute(text("""
        SELECT COUNT(*) FROM paper_fills
        WHERE code='0050' AND action='SELL' AND account_id < 11
    """)).scalar()
    if r5:
        warns.append(f"⚠️ 0050 被賣出 {r5} 次（V4 舊帳戶）")
    else:
        print("✅ 0050 未被強制賣出")

    # 6. equity_curve 無未來日期
    r6 = db.execute(text("""
        SELECT COUNT(*) FROM equity_curve WHERE snap_date > date('now','localtime')
    """)).scalar()
    if r6:
        issues.append(f"❌ equity_curve 有 {r6} 筆未來日期")
    else:
        print("✅ equity_curve 無未來日期")

    print()
    if issues:
        print("=== ❌ FAIL ===")
        for i in issues: print(" ", i)
    if warns:
        print("=== ⚠️ WARN ===")
        for w in warns: print(" ", w)
    if not issues and not warns:
        print("🎉 全部通過，無偷看未來問題")

    print(f"\nFAIL={len(issues)} WARN={len(warns)}")
    db.close()
    return {"fail": len(issues), "warn": len(warns), "issues": issues, "warns": warns}

if __name__ == "__main__":
    run()
