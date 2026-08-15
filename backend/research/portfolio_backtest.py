"""
組合層級回測引擎(複用前向決策鏈,確保回測=實測)
核心設計:逐日呼叫 generate_strategy_decisions → simulate_paper_fills → update_v5_equity
        帳戶隔離:只操作 account_id >= 100

限制(誠實聲明):
- 不含息(price return);所有策略與基準同樣不含息,相對比較有效
- universe 過濾與漲跌停鎖死尚未加入(Part 2 後續補)
用法:
  python3 -m backend.research.portfolio_backtest --account 101 --start 2026-05-25 --end 2026-08-03
"""
import argparse, sys, time
from datetime import date
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.v5.decision_engine import generate_strategy_decisions
from backend.v5.paper_engine import simulate_paper_fills, update_v5_equity


def trading_days(start: str, end: str):
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date BETWEEN :s AND :e AND code GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY trade_date"""), {"s": start, "e": end}).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        db.close()


def run_backtest(account_id: int, start: str, end: str, verbose: bool = True):
    if account_id < 100:
        raise SystemExit(f"❌ 拒絕回測 account_id={account_id}:100 以下為前向實測帳戶")

    days = trading_days(start, end)
    if not days:
        raise SystemExit(f"❌ {start}~{end} 無交易日資料")

    t0 = time.time()
    import datetime as _dt0
    _bt_started_at = _dt0.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"=== 回測帳戶 {account_id} | {start} ~ {end} | {len(days)} 個交易日 ===")

    for i, d in enumerate(days, 1):
        dd = date.fromisoformat(d)
        generate_strategy_decisions(dd, account_min=account_id, account_max=account_id)
        simulate_paper_fills(dd, account_min=account_id, account_max=account_id)
        update_v5_equity(dd, account_min=account_id, account_max=account_id)
        if verbose and (i % 20 == 0 or i == len(days)):
            print(f"  [{d}] {i}/{len(days)} ({time.time()-t0:.0f}s)")

    # POLLUTION_GUARD:回測結束立刻檢查前向帳戶是否被觸碰
    db = SessionLocal()
    try:
        import datetime as _dt
        # GUARD_V2:用「回測實際開始時間」當基準,不用固定 30 分鐘窗
        since = _bt_started_at
        n = db.execute(text("""
            SELECT COUNT(*) FROM paper_fills
            WHERE account_id < 100 AND created_at >= :s"""), {"s": since}).scalar() or 0
    finally:
        db.close()
    if n > 0:
        print(f"\n🚨 警告:前向帳戶被寫入 {n} 筆 fills!回測污染了實測資料,必須立即回滾。")
    else:
        print("\n✓ 隔離檢查通過:前向帳戶未被觸碰")

    return summarize(account_id, start, end)


def summarize(account_id: int, start: str, end: str):
    db = SessionLocal()
    try:
        curve = db.execute(text("""
            SELECT snap_date, total_equity FROM equity_curve
            WHERE account_id=:a AND snap_date BETWEEN :s AND :e ORDER BY snap_date"""),
            {"a": account_id, "s": start, "e": end}).fetchall()
        if not curve:
            return {"error": "無淨值資料"}
        init = db.execute(text("SELECT initial_cash FROM strategy_accounts WHERE id=:a"),
                          {"a": account_id}).scalar() or 200000.0
        first, last = float(curve[0][1]), float(curve[-1][1])

        peak, mdd = -1e18, 0.0
        for _, eq in curve:
            eq = float(eq)
            peak = max(peak, eq)
            if peak > 0:
                mdd = min(mdd, (eq / peak - 1) * 100)

        sells = db.execute(text("""
            SELECT COUNT(*) FROM paper_fills WHERE account_id=:a AND action='SELL'"""),
            {"a": account_id}).scalar() or 0
        fees = db.execute(text("""
            SELECT COALESCE(SUM(fee),0)+COALESCE(SUM(tax),0) FROM paper_fills WHERE account_id=:a"""),
            {"a": account_id}).scalar() or 0

        res = {
            "account_id": account_id, "start": start, "end": end,
            "days": len(curve),
            "initial": round(init),
            "final": round(last),
            "total_return_pct": round((last / init - 1) * 100, 2),
            "max_drawdown_pct": round(mdd, 2),
            "sell_count": sells,
            "total_fees": round(float(fees)),
            "note": "不含息(price return);universe/漲跌停過濾尚未加入",
        }
        print("\n=== 回測結果 ===")
        for k, v in res.items():
            print(f"  {k}: {v}")
        return res
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--summary-only", action="store_true", help="只輸出摘要,不重跑")
    args = ap.parse_args()

    if args.summary_only:
        summarize(args.account, args.start, args.end)
    else:
        run_backtest(args.account, args.start, args.end)


if __name__ == "__main__":
    main()
