"""scripts/v5_daily_pipeline.py
V5 每日完整 Pipeline（在 v4_3_run_daily_workflow 之後執行）

流程：
1. 檢查停損/停利 → 產生 SELL 決策
2. generate_strategy_decisions → 產生 BUY 決策
3. simulate_paper_fills → 執行昨日 pending 成交
4. update_v5_equity → 更新帳戶估值
5. 輸出摘要
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from loguru import logger
from backend.utils.trading_day import is_trading_day, latest_open_trade_date


def run(target_date: date = None):
    if target_date is None:
        target_date = date.today()

    print(f"\n=== V5 每日 Pipeline {target_date} ===\n")

    # Step 1: 停損/停利檢查
    print("Step 1: 停損/停利檢查...")
    from backend.v5.paper_engine import check_stop_loss_take_profit
    r1 = check_stop_loss_take_profit(target_date)
    print(f"  → {r1.get('sells_generated', 0)} 筆 SELL 決策")

    # Step 2: 產生今日 BUY 決策
    print("Step 2: 產生策略決策...")
    from backend.v5.decision_engine import generate_strategy_decisions
    r2 = generate_strategy_decisions(target_date)
    print(f"  → {r2.get('decisions', 0)} 筆決策")

    # Step 3: 模擬成交昨日 pending（execution_date = today）
    print("Step 3: 模擬 T+1 成交...")
    from backend.v5.paper_engine import simulate_paper_fills
    r3 = simulate_paper_fills(target_date)
    print(f"  → 成交 {r3.get('filled', 0)} 筆，錯誤 {len(r3.get('errors', []))} 筆")
    if r3.get('errors'):
        for e in r3['errors'][:3]:
            print(f"     ⚠️ {e}")

    # Step 4: 更新估值
    print("Step 4: 更新 equity_curve...")
    from backend.v5.paper_engine import update_v5_equity
    r4 = update_v5_equity(target_date)
    print(f"  → 更新 {r4.get('updated', 0)} 個帳戶")

    # Step 5: 更新 0050 benchmark
    print("Step 5: 更新 0050 benchmark...")
    from backend.v5.benchmark import rebuild_0050_benchmark
    n = rebuild_0050_benchmark(start_date="2025-01-01")
    print(f"  → {n} 筆")

    # 摘要
    print("\n=== V5 帳戶狀況 ===")
    from backend.models.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    rows = db.execute(text("""
        SELECT a.id, a.name, a.cash,
               eq.market_value, eq.total_equity, eq.daily_return
        FROM strategy_accounts a
        LEFT JOIN (
            SELECT account_id, market_value, total_equity, daily_return
            FROM equity_curve WHERE snap_date=:d
        ) eq ON eq.account_id=a.id
        WHERE a.id >= 11
        ORDER BY eq.total_equity DESC NULLS LAST
    """), {"d": str(target_date)}).fetchall()

    for r in rows:
        total = float(r[4] or 200000)
        ret = (total / 200000 - 1) * 100
        dr = float(r[5] or 0)
        print(f"  A{r[0]} {r[1]:25} 總資產={total:>10,.0f} "
              f"累積={ret:+.2f}% 今日={dr:+.2f}%")

    # 0050 benchmark 比較
    bench = db.execute(text("""
        SELECT cumulative_return FROM benchmark_daily_equity
        WHERE benchmark_code='0050' AND snap_date=:d
    """), {"d": str(target_date)}).scalar()
    if bench:
        print(f"\n  📊 0050 同期累積：{float(bench):+.2f}%")
    db.close()

    print(f"\n✅ V5 Pipeline 完成")
    return {"ok": True, "decisions": r2.get("decisions", 0), "filled": r3.get("filled", 0)}


if __name__ == "__main__":
    import sys
    td = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    run(td)
