"""scripts/v6_update_strategy_health_scores.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal


def compute_health(alpha, max_dd, win_rate, profit_factor, trade_count, rolling_alpha=0) -> tuple:
    """計算健康分數（0~100）和建議"""
    # 分項分數
    alpha_score    = min(100, max(0, 50 + alpha * 5))
    dd_score       = min(100, max(0, 100 - max_dd * 3))
    wr_score       = win_rate
    pf_score       = min(100, max(0, (profit_factor - 1) * 30 + 50))
    sample_score   = min(100, trade_count * 10)
    rolling_score  = min(100, max(0, 50 + rolling_alpha * 5))

    health = (alpha_score * 0.30 + dd_score * 0.20 + pf_score * 0.15 +
              wr_score * 0.10 + sample_score * 0.10 + rolling_score * 0.15)
    health = round(health, 1)

    # 建議
    reasons = []
    if trade_count < 3:
        rec = "KEEP"
        reasons.append(f"交易次數不足（{trade_count}筆），樣本有限")
    elif health >= 80 and alpha > 3 and max_dd < 5:
        rec = "PROMOTE"
        reasons.append(f"Alpha={alpha:+.1f}% 健康={health}")
    elif health >= 60:
        rec = "KEEP"
        reasons.append(f"健康分 {health}，持續觀察")
    elif health >= 40:
        rec = "REDUCE"
        reasons.append(f"Alpha={alpha:+.1f}% 健康分偏低 {health}")
    elif max_dd > 20:
        rec = "PAUSE"
        reasons.append(f"最大回撤過高 {max_dd:.1f}%")
    else:
        rec = "PAUSE"
        reasons.append(f"健康分 {health} 低於門檻")

    if alpha < -5: reasons.append(f"持續跑輸0050 {alpha:+.1f}%")
    if profit_factor < 1: reasons.append(f"獲利因子 < 1（{profit_factor:.2f}）")

    return health, rec, "；".join(reasons)


def update_health_scores(eval_days=30):
    db = SessionLocal()
    try:
        today = date.today()
        start = str(today - timedelta(days=eval_days))
        print(f"計算策略健康分數（{start} ~ {today}）...")

        accounts = db.execute(text(
            "SELECT id, name FROM strategy_accounts WHERE id >= 11"
        )).fetchall()

        bench_ret = db.execute(text("""
            SELECT (MAX(cumulative_return) - MIN(cumulative_return))
            FROM benchmark_daily_equity
            WHERE snap_date >= :sd AND benchmark_code='0050'
        """), {"sd": start}).scalar() or 0

        for aid, aname in accounts:
            # equity 報酬
            eq_rows = db.execute(text("""
                SELECT total_equity FROM equity_curve
                WHERE account_id=:id AND snap_date>=:sd
                ORDER BY snap_date
            """), {"id": aid, "sd": start}).fetchall()

            if not eq_rows:
                continue

            start_eq = float(eq_rows[0][0] or 200000)
            end_eq   = float(eq_rows[-1][0] or start_eq)
            ret = (end_eq/start_eq - 1)*100 if start_eq else 0
            alpha = ret - float(bench_ret or 0)

            # 最大回撤
            peak = start_eq; max_dd = 0
            for (eq,) in eq_rows:
                eq_f = float(eq or peak)
                if eq_f > peak: peak = eq_f
                dd = (peak-eq_f)/peak*100 if peak else 0
                if dd > max_dd: max_dd = dd

            # 勝率、profit factor（從 paper_fills）
            fills = db.execute(text("""
                SELECT pf.fill_price, p2.avg_cost
                FROM paper_fills pf
                LEFT JOIN positions p2 ON p2.account_id=pf.account_id AND p2.code=pf.code
                WHERE pf.account_id=:id AND pf.action='SELL'
                  AND pf.execution_date >= :sd
            """), {"id": aid, "sd": start}).fetchall()

            trade_count = len(fills)
            win_rate = 50.0
            profit_factor = 1.0
            avg_win = avg_loss = 0

            if trade_count > 0:
                wins_amt = []; loss_amt = []
                for fp, ac in fills:
                    pnl = (float(fp or 0) / float(ac or fp or 1) - 1) * 100
                    if pnl > 0: wins_amt.append(pnl)
                    else: loss_amt.append(abs(pnl))
                win_rate = len(wins_amt)/trade_count*100
                _loss_sum = sum(loss_amt)
                profit_factor = sum(wins_amt)/_loss_sum if _loss_sum > 0 else 99.0

            health, rec, reason = compute_health(
                alpha, max_dd, win_rate, profit_factor, trade_count, alpha
            )

            db.execute(text("""
                INSERT INTO strategy_health_scores
                    (strategy_name, account_id, eval_start_date, eval_end_date,
                     alpha_vs_0050, max_drawdown, win_rate, profit_factor,
                     trade_count, rolling_alpha_20d, health_score,
                     recommendation, reason_summary)
                VALUES (:sn,:aid,:sd,:ed,:al,:md,:wr,:pf,:tc,:ra,:hs,:rec,:rs)
            """), {
                "sn": aname, "aid": aid, "sd": start, "ed": str(today),
                "al": round(alpha,2), "md": round(max_dd,2),
                "wr": round(win_rate,1), "pf": round(profit_factor,3),
                "tc": trade_count, "ra": round(alpha,2),
                "hs": health, "rec": rec, "rs": reason,
            })

            icon = {"PROMOTE":"🚀","KEEP":"✅","REDUCE":"⚠️","PAUSE":"🛑"}.get(rec,"❓")
            print(f"  {icon} A{aid} {aname:25} 健康={health:5.1f} {rec:7} alpha={alpha:+.2f}%")

        db.commit()
        print("✓ 策略健康分數更新完成")

    finally:
        db.close()

if __name__ == "__main__":
    update_health_scores()
