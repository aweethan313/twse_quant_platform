"""scripts/v6_update_cooldowns.py - 停損冷卻期管理"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal

COOLDOWN_DAYS = {"conservative": 10, "normal": 5, "aggressive": 3}

def update_cooldowns(signal_date: date = None):
    if not signal_date: signal_date = date.today()
    db = SessionLocal()
    try:
        # 找今日停損賣出
        stops = db.execute(text("""
            SELECT pf.account_id, pf.code, sm.name, pf.fill_price,
                   cfg.stop_loss_pct, cfg.strategy_name
            FROM paper_fills pf
            LEFT JOIN stock_meta sm ON sm.code=pf.code
            LEFT JOIN strategy_account_configs cfg ON cfg.account_id=pf.account_id
            WHERE pf.execution_date=:d AND pf.action='SELL'
              AND pf.note LIKE '%停損%'
        """), {"d": str(signal_date)}).fetchall()

        added = 0
        for aid, code, name, exit_price, sl_pct, sname in stops:
            cooldown_days = COOLDOWN_DAYS.get("normal", 5)
            cooldown_until = str(signal_date + timedelta(days=cooldown_days))
            existing = db.execute(text("""
                SELECT id FROM strategy_cooldowns
                WHERE account_id=:id AND code=:c AND is_active=1
            """), {"id": aid, "c": code}).fetchone()
            if not existing:
                db.execute(text("""
                    INSERT INTO strategy_cooldowns
                        (account_id, strategy_name, code, stock_name,
                         triggered_date, exit_price, cooldown_days,
                         cooldown_until, reason, is_active)
                    VALUES (:aid,:sn,:c,:n,:d,:ep,:cd,:cu,:reason,1)
                """), {
                    "aid": aid, "sn": sname, "c": code, "n": name,
                    "d": str(signal_date), "ep": exit_price,
                    "cd": cooldown_days, "cu": cooldown_until,
                    "reason": "STOP_LOSS",
                })
                added += 1

        # 解除過期 cooldown
        lifted = db.execute(text("""
            UPDATE strategy_cooldowns SET is_active=0, lifted_date=:d, lifted_reason='EXPIRED'
            WHERE is_active=1 AND cooldown_until < :d
        """), {"d": str(signal_date)}).rowcount

        db.commit()
        print(f"[COOLDOWN] {signal_date} 新增={added} 解除={lifted}")
        return {"added": added, "lifted": lifted}
    finally:
        db.close()

def is_in_cooldown(account_id: int, code: str, check_date: date = None) -> bool:
    if not check_date: check_date = date.today()
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT id FROM strategy_cooldowns
            WHERE account_id=:id AND code=:c AND is_active=1
              AND cooldown_until >= :d
        """), {"id": account_id, "c": code, "d": str(check_date)}).fetchone()
        return r is not None
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    update_cooldowns(d)
