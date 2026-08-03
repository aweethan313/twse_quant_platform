"""
回測帳戶管理(方案A:account_id >= 100 與前向帳戶隔離)
- setup:從指定前向帳戶複製 config 到回測帳戶
- reset:清空回測帳戶的所有痕跡,回到初始狀態(可反覆回測)
用法:
  python3 -m scripts.backtest_setup --source 11 --target 101 --cash 200000
  python3 -m scripts.backtest_setup --reset 101
  python3 -m scripts.backtest_setup --list
"""
import argparse, sys
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal

FORWARD_MAX = 99   # 100 以上為回測專用


def _guard(account_id: int):
    if account_id < 100:
        raise SystemExit(f"❌ 拒絕操作 account_id={account_id}:100 以下為前向實測帳戶,不可觸碰")


def reset(account_id: int, initial_cash: float = None):
    """清空回測帳戶的所有交易痕跡"""
    _guard(account_id)
    db = SessionLocal()
    try:
        if initial_cash is None:
            initial_cash = db.execute(text(
                "SELECT initial_cash FROM strategy_accounts WHERE id=:a"), {"a": account_id}).scalar() or 200000.0
        for tbl in ["positions", "paper_fills", "strategy_decision_logs", "equity_curve", "dividend_income"]:
            try:
                db.execute(text(f"DELETE FROM {tbl} WHERE account_id=:a"), {"a": account_id})
            except Exception as e:
                print(f"  (跳過 {tbl}: {e})")
        db.execute(text("UPDATE strategy_accounts SET cash=:c, realized_pnl=0, unrealized_pnl=0 WHERE id=:a"),
                   {"c": float(initial_cash), "a": account_id})
        db.commit()
        print(f"✓ 帳戶 {account_id} 已重置,現金={initial_cash:,.0f}")
    finally:
        db.close()


def setup(source_id: int, target_id: int, cash: float, start_date: str):
    """複製前向帳戶的策略設定到回測帳戶"""
    _guard(target_id)
    db = SessionLocal()
    try:
        src = db.execute(text(
            "SELECT name, strategy_type FROM strategy_accounts WHERE id=:s"), {"s": source_id}).fetchone()
        if not src:
            raise SystemExit(f"❌ 來源帳戶 {source_id} 不存在")
        name = f"BT{target_id} {src[0]}"

        db.execute(text("""
            INSERT INTO strategy_accounts(id, name, strategy_type, initial_cash, cash,
                                          is_active, start_date, mode, benchmark)
            VALUES(:id,:n,:st,:c,:c,1,:sd,'backtest','0050')
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, strategy_type=excluded.strategy_type,
              initial_cash=excluded.initial_cash, cash=excluded.cash,
              is_active=1, start_date=excluded.start_date, mode='backtest'
        """), {"id": target_id, "n": name, "st": src[1], "c": cash, "sd": start_date})

        # 複製 config(除 id / account_id 外全部照抄)
        cols = [r[1] for r in db.execute(text("PRAGMA table_info(strategy_account_configs)")).fetchall()]
        copy_cols = [c for c in cols if c not in ("id", "account_id")]
        col_list = ", ".join(copy_cols)
        db.execute(text(f"DELETE FROM strategy_account_configs WHERE account_id=:t"), {"t": target_id})
        db.execute(text(f"""
            INSERT INTO strategy_account_configs(account_id, {col_list})
            SELECT :t, {col_list} FROM strategy_account_configs WHERE account_id=:s
        """), {"t": target_id, "s": source_id})
        db.execute(text("UPDATE strategy_account_configs SET is_active=1, mode='backtest' WHERE account_id=:t"),
                   {"t": target_id})
        db.commit()
        print(f"✓ 回測帳戶 {target_id}「{name}」建立完成(複製自 {source_id},現金={cash:,.0f})")
    finally:
        db.close()
    reset(target_id, cash)


def list_accounts():
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT a.id, a.name, a.mode, a.cash, c.strategy_name, c.is_active
            FROM strategy_accounts a
            LEFT JOIN strategy_account_configs c ON c.account_id=a.id
            WHERE a.id >= 100 ORDER BY a.id""")).fetchall()
        if not rows:
            print("(尚無回測帳戶)")
        for r in rows:
            print(f"  {r[0]} | {r[1]} | mode={r[2]} | cash={r[3]:,.0f} | strat={r[4]} | active={r[5]}")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=int, help="來源前向帳戶 id(如 11=A1)")
    ap.add_argument("--target", type=int, help="回測帳戶 id(需 >= 100)")
    ap.add_argument("--cash", type=float, default=200000.0)
    ap.add_argument("--start-date", default="2017-01-03")
    ap.add_argument("--reset", type=int, help="重置指定回測帳戶")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        list_accounts()
    elif args.reset:
        reset(args.reset)
    elif args.source and args.target:
        setup(args.source, args.target, args.cash, args.start_date)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
