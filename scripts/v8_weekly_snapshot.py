"""scripts/v8_weekly_snapshot.py - 週績效快照"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal


def take_weekly_snapshot(week_end: date = None):
    if not week_end:
        week_end = date.today()
    week_start = week_end - timedelta(days=6)
    db = SessionLocal()
    try:
        # 取 0050 週報酬
        bench = db.execute(text("""
            SELECT MIN(cumulative_return), MAX(cumulative_return)
            FROM benchmark_daily_equity
            WHERE snap_date >= :s AND snap_date <= :e AND benchmark_code='0050'
        """), {"s": str(week_start), "e": str(week_end)}).fetchone()
        bench_start = float(bench[0] or 0)
        bench_end   = float(bench[1] or 0)
        bench_ret   = bench_end - bench_start

        accounts = db.execute(text("SELECT id, name FROM strategy_accounts WHERE id >= 11")).fetchall()
        snaps = 0

        for aid, aname in accounts:
            rows = db.execute(text("""
                SELECT snap_date, total_equity FROM equity_curve
                WHERE account_id=:id AND snap_date >= :s AND snap_date <= :e
                ORDER BY snap_date
            """), {"id": aid, "s": str(week_start), "e": str(week_end)}).fetchall()

            if len(rows) < 2: continue

            eq_start = float(rows[0][1])
            eq_end   = float(rows[-1][1])
            weekly_ret = (eq_end/eq_start - 1)*100 if eq_start else 0
            alpha = weekly_ret - bench_ret

            fills = db.execute(text("""
                SELECT action, fill_price FROM paper_fills
                WHERE account_id=:id AND execution_date >= :s AND execution_date <= :e
            """), {"id": aid, "s": str(week_start), "e": str(week_end)}).fetchall()

            wins = sum(1 for f in fills if f[0]=='SELL')
            trade_count = len([f for f in fills if f[0]=='SELL'])

            db.execute(text("""
                INSERT INTO weekly_performance_snapshots
                    (week_start, week_end, account_id, strategy_name,
                     equity_start, equity_end, weekly_return, benchmark_return,
                     alpha, trade_count)
                VALUES (:ws,:we,:aid,:aname,:es,:ee,:wr,:br,:al,:tc)
                ON CONFLICT(week_start, account_id) DO UPDATE SET
                    equity_end=excluded.equity_end,
                    weekly_return=excluded.weekly_return,
                    alpha=excluded.alpha
            """), {"ws": str(week_start), "we": str(week_end),
                   "aid": aid, "aname": aname,
                   "es": eq_start, "ee": eq_end,
                   "wr": round(weekly_ret,3), "br": round(bench_ret,3),
                   "al": round(alpha,3), "tc": trade_count})
            snaps += 1

            icon = "✅" if alpha > 0 else "❌"
            print(f"  {icon} {aname:25} 週報酬={weekly_ret:+.2f}% alpha={alpha:+.2f}%")

        db.commit()
        print(f"\n[WEEKLY] {week_start}~{week_end} 快照完成：{snaps}個帳戶")
        return snaps
    finally:
        db.close()


def auto_strategy_action(db=None):
    """根據健康分數自動執行升降格"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        rows = db.execute(text("""
            SELECT account_id, strategy_name, health_score, recommendation
            FROM strategy_health_scores
            WHERE id IN (
                SELECT MAX(id) FROM strategy_health_scores GROUP BY account_id
            )
        """)).fetchall()

        actions = []
        for aid, sname, health, rec in rows:
            if rec == "PAUSE" and health < 35:
                actions.append({"account_id": aid, "action": "REDUCE_WEIGHT",
                                 "reason": f"健康分={health} < 35，自動降低交易頻率"})
            elif rec == "PROMOTE" and health >= 80:
                actions.append({"account_id": aid, "action": "INCREASE_WEIGHT",
                                 "reason": f"健康分={health} >= 80，表現優秀"})

        for a in actions:
            print(f"  [AUTO] A{a['account_id']} → {a['action']}: {a['reason']}")

        return actions
    finally:
        if close_db: db.close()


if __name__ == "__main__":
    take_weekly_snapshot()
    auto_strategy_action()
