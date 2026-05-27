"""scripts/v8_bear_market_stress.py - 空頭市場壓力測試"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal

# 歷史空頭期
BEAR_PERIODS = [
    {"name": "2022 熊市", "start": "2022-01-01", "end": "2022-12-31"},
    {"name": "2022 Q2 崩跌", "start": "2022-04-01", "end": "2022-06-30"},
    {"name": "2025 關稅崩跌", "start": "2025-04-01", "end": "2025-04-30"},
]


def run_bear_stress():
    db = SessionLocal()
    try:
        # 取所有策略設定
        configs = db.execute(text("""
            SELECT a.id, a.name, cfg.strategy_name,
                   cfg.stop_loss_pct, cfg.min_score, cfg.max_positions
            FROM strategy_accounts a
            JOIN strategy_account_configs cfg ON cfg.account_id=a.id
            WHERE a.id >= 11
        """)).fetchall()

        # 0050 在各空頭期的表現
        print("=== 空頭市場壓力測試 ===\n")
        results = []

        for period in BEAR_PERIODS:
            print(f"📉 {period['name']} ({period['start']} ~ {period['end']})")

            bench_rows = db.execute(text("""
                SELECT MIN(snap_date), MAX(snap_date),
                       MIN(cumulative_return), MAX(cumulative_return)
                FROM benchmark_daily_equity
                WHERE benchmark_code='0050'
                  AND snap_date >= :s AND snap_date <= :e
                  AND (is_valid=1 OR is_valid IS NULL)
            """), {"s": period["start"], "e": period["end"]}).fetchone()

            if not bench_rows or not bench_rows[0]:
                print(f"  ⚠️ {period['name']} 無 benchmark 資料（可能超出資料範圍）")
                continue

            bench_ret = float((bench_rows[3] or 0)) - float((bench_rows[2] or 0))
            print(f"  0050: {bench_ret:+.2f}%")

            # 用回測邏輯跑各策略
            from scripts.v6_backtest_validate_strategies import run_strategy_backtest

            trade_dates = db.execute(text("""
                SELECT DISTINCT trade_date FROM trading_calendar
                WHERE is_open=1 AND trade_date >= :s AND trade_date <= :e
                ORDER BY trade_date
            """), {"s": period["start"], "e": period["end"]}).fetchall()

            if len(trade_dates) < 5:
                print(f"  ⚠️ {period['name']} 交易日不足")
                continue

            period_results = []
            for row in configs:
                aid, aname, sname, sl, ms, mp = row
                cfg = {"stop_loss_pct": float(sl or 0.08),
                       "min_score": float(ms or 65),
                       "max_positions": int(mp or 5),
                       "max_position_pct": 0.20,
                       "take_profit_pct": 0.15,
                       "large_cap_only": 0, "no_chase_enabled": 0,
                       "max_rsi14": 80, "min_rsi14": 30,
                       "max_distance_ma20_pct": 12,
                       "theme_filter": None, "candidate_rank_limit": 5}
                try:
                    r = run_strategy_backtest(db, sname, cfg, trade_dates)
                    alpha = r["total_return"] - bench_ret
                    beat = "✅" if alpha > 0 else "❌"
                    print(f"  {beat} {aname:25} {r['total_return']:+.2f}% alpha={alpha:+.2f}%")

                    db.execute(text("""
                        INSERT INTO bear_market_stress_test
                            (strategy_name, test_period, start_date, end_date,
                             strategy_return, benchmark_return, alpha, max_drawdown)
                        VALUES (:sn,:tp,:sd,:ed,:sr,:br,:al,:md)
                    """), {"sn": sname, "tp": period["name"],
                           "sd": period["start"], "ed": period["end"],
                           "sr": r["total_return"], "br": bench_ret,
                           "al": round(r["total_return"]-bench_ret,3),
                           "md": r["max_drawdown"]})
                    period_results.append({"strategy": aname, "return": r["total_return"], "alpha": alpha})
                except Exception as e:
                    print(f"  ❌ {aname}: {e}")

            db.commit()
            results.append({"period": period["name"], "bench": bench_ret, "strategies": period_results})

        # 輸出報告
        path = Path("data/reports/v8_bear_market_stress.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\n✓ 壓力測試報告：{path}")

    finally:
        db.close()


if __name__ == "__main__":
    run_bear_stress()
