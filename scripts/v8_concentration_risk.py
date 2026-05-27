"""scripts/v8_concentration_risk.py - 選股集中度風險"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal


def check_concentration(signal_date: date = None):
    if not signal_date: signal_date = date.today()
    db = SessionLocal()
    try:
        # 找今日各策略選到哪些股
        rows = db.execute(text("""
            SELECT sdl.code, sm.name,
                   COUNT(DISTINCT sdl.account_id) as acct_count,
                   GROUP_CONCAT(DISTINCT sdl.account_id) as accounts
            FROM strategy_decision_logs sdl
            LEFT JOIN stock_meta sm ON sm.code=sdl.code
            WHERE sdl.signal_date=:d AND sdl.action='BUY' AND sdl.is_blocked=0
            GROUP BY sdl.code ORDER BY acct_count DESC
        """), {"d": str(signal_date)}).fetchall()

        risks = []
        for code, name, cnt, accounts in rows:
            risk = "HIGH" if cnt >= 4 else "MEDIUM" if cnt >= 2 else "LOW"
            risks.append({"code": code, "name": name, "count": cnt,
                          "accounts": accounts, "risk": risk})

            db.execute(text("""
                INSERT INTO selection_concentration
                    (check_date, code, stock_name, selected_by_count,
                     selected_by_accounts, concentration_risk)
                VALUES (:d,:c,:n,:cnt,:accts,:risk)
            """), {"d": str(signal_date), "c": code, "n": name,
                   "cnt": cnt, "accts": accounts, "risk": risk})

        db.commit()

        high = [r for r in risks if r["risk"] == "HIGH"]
        med  = [r for r in risks if r["risk"] == "MEDIUM"]
        print(f"[CONCENTRATION] {signal_date} HIGH={len(high)} MED={len(med)}")
        for r in high:
            print(f"  🚨 {r['code']} {r['name']} 被 {r['count']} 個策略同時選中（A{r['accounts']}）")
        return risks
    finally:
        db.close()


if __name__ == "__main__":
    check_concentration()
