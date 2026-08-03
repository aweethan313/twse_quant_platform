"""
回滾 2026-08-03 回測污染事件
- 刪除 created_at >= '2026-08-03 20:05' 且 account_id 11-17 的 18 筆 fills
- 重播剩餘 fills 重建 cash / positions(權威來源=paper_fills)
- 重算 5/25~今 的 equity_curve
- 驗證 A1 7/31 淨值回到 248,775

安全設計:只碰 11-17,每步印出前後對照,可先 --dry-run 檢視
用法:
  python3 rollback_backtest_pollution.py --dry-run
  python3 rollback_backtest_pollution.py --apply
"""
import argparse, sys
from datetime import date
from pathlib import Path
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal

CUTOFF = '2026-08-03 20:32'
ACCOUNTS = [11, 12, 13, 14, 15, 16, 17]


def main(apply: bool):
    db = SessionLocal()
    try:
        # ── 1. 檢視待刪 ──
        bad = db.execute(text("""
            SELECT id, account_id, execution_date, code, action, shares
            FROM paper_fills
            WHERE created_at >= :c AND account_id BETWEEN 11 AND 17
            ORDER BY account_id, execution_date"""), {"c": CUTOFF}).fetchall()
        print(f"待刪污染 fills:{len(bad)} 筆")
        for r in bad:
            print(f"  A{r[1]} {r[2]} {r[3]} {r[4]} {r[5]}股")
        if not apply:
            print("\n(dry-run,未執行。加 --apply 才會修改)")
            return

        # ── 2. 刪除 ──
        db.execute(text("""
            DELETE FROM paper_fills
            WHERE created_at >= :c AND account_id BETWEEN 11 AND 17"""), {"c": CUTOFF})
        db.commit()
        print(f"\n✓ 已刪除 {len(bad)} 筆污染 fills")

        # ── 3. 重播剩餘 fills 重建 cash / positions ──
        print("\n重建 cash / positions(重播 paper_fills)...")
        for aid in ACCOUNTS:
            init_cash = db.execute(text(
                "SELECT initial_cash FROM strategy_accounts WHERE id=:a"), {"a": aid}).scalar() or 200000.0
            fills = db.execute(text("""
                SELECT code, action, shares, fill_price, fee, tax, gross_amount, net_amount
                FROM paper_fills
                WHERE account_id=:a AND COALESCE(is_blocked,0)=0
                ORDER BY execution_date, id"""), {"a": aid}).fetchall()

            cash = float(init_cash)
            pos = {}   # code -> [shares, total_cost]
            for code, action, sh, fp, fee, tax, gross, net in fills:
                sh = float(sh or 0); fp = float(fp or 0)
                if sh <= 0:
                    continue
                if action == 'BUY':
                    cost = float(gross) if gross else fp * sh
                    cash -= (cost + float(fee or 0))
                    s, c = pos.get(code, (0.0, 0.0))
                    pos[code] = (s + sh, c + cost + float(fee or 0))
                elif action == 'SELL':
                    s, c = pos.get(code, (0.0, 0.0))
                    if s <= 0:
                        continue
                    sell_sh = min(sh, s)
                    proceeds = float(net) if net else fp * sell_sh - float(fee or 0) - float(tax or 0)
                    cash += proceeds
                    avg = c / s
                    pos[code] = (s - sell_sh, c - avg * sell_sh)

            # 加回股息
            div = db.execute(text(
                "SELECT COALESCE(SUM(amount),0) FROM dividend_income WHERE account_id=:a"), {"a": aid}).scalar() or 0
            cash += float(div)

            old_cash = db.execute(text("SELECT cash FROM strategy_accounts WHERE id=:a"), {"a": aid}).scalar()
            db.execute(text("UPDATE strategy_accounts SET cash=:c WHERE id=:a"), {"c": round(cash, 2), "a": aid})

            db.execute(text("DELETE FROM positions WHERE account_id=:a"), {"a": aid})
            n_pos = 0
            for code, (s, c) in pos.items():
                if s > 0.5:
                    db.execute(text("""
                        INSERT INTO positions(account_id, code, lots, avg_cost)
                        VALUES(:a,:c,:l,:ac)"""),
                        {"a": aid, "c": code, "l": int(round(s)), "ac": round(c / s, 4)})
                    n_pos += 1
            print(f"  A{aid}: cash {float(old_cash):,.0f} → {cash:,.0f} | 持倉 {n_pos} 檔 | 股息 {float(div):,.0f}")
        db.commit()

        # ── 4. 重算 equity_curve ──
        print("\n重算 equity_curve(5/25 ~ 今)...")
        from backend.v5.paper_engine import update_v5_equity
        days = [str(r[0]) for r in db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date >= '2026-05-25' AND code GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY trade_date""")).fetchall()]
        db.close()
        for d in days:
            update_v5_equity(date.fromisoformat(d), account_min=11, account_max=17)
        print(f"  ✓ 已重算 {len(days)} 天")

        # ── 5. 驗證 ──
        db2 = SessionLocal()
        print("\n=== 驗證 ===")
        chk = db2.execute(text("""
            SELECT snap_date, ROUND(total_equity) FROM equity_curve
            WHERE account_id=11 AND snap_date IN ('2026-07-31','2026-08-03')
            ORDER BY snap_date""")).fetchall()
        for d, e in chk:
            print(f"  A1 {d}: {e:,.0f}")
        print("  (A1 7/31 應為 248,775)")
        db2.close()
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not (a.dry_run or a.apply):
        ap.print_help()
    else:
        main(a.apply)
