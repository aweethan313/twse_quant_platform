"""scripts/v8_param_grid_search.py
策略參數 Grid Search：找最佳停損/分數門檻/持倉數
用 2025 全年資料窮舉最佳參數組合
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from itertools import product
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal
from loguru import logger


PARAM_GRID = {
    "min_score":        [55, 60, 65, 70, 75, 80],
    "max_positions":    [3, 5, 7, 10],
    "stop_loss_pct":    [0.05, 0.08, 0.10, 0.12, 0.15],
    "take_profit_pct":  [0.10, 0.15, 0.20, 0.25],
    "hold_days":        [3, 5, 10],
}


def backtest_params(db, trade_dates, params):
    """快速參數回測"""
    if not trade_dates:
        return None

    initial = 200000.0
    equity = initial
    peak = initial
    max_dd = 0
    wins = losses = 0
    total_trades = 0
    hold = params["hold_days"]

    for i in range(0, len(trade_dates), max(1, hold//2)):
        signal_date = trade_dates[i]

        rows = db.execute(text("""
            SELECT code, return_5d, final_score
            FROM candidate_forward_returns
            WHERE signal_date=:d
              AND final_score >= :ms
              AND return_5d IS NOT NULL
            ORDER BY final_score DESC
            LIMIT :lim
        """), {"d": signal_date, "ms": params["min_score"],
               "lim": params["max_positions"]}).fetchall()

        if not rows:
            continue

        pos_size = equity / len(rows)
        for r in rows:
            ret = float(r[1]) / 100
            ret = max(ret, -params["stop_loss_pct"])
            ret = min(ret,  params["take_profit_pct"])
            pnl = pos_size * ret
            equity += pnl
            total_trades += 1
            if pnl > 0: wins += 1
            else: losses += 1
            if equity > peak: peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd: max_dd = dd

        if equity <= 0:
            break

    if total_trades == 0:
        return None

    return {
        "total_return": round((equity/initial-1)*100, 2),
        "win_rate": round(wins/total_trades*100, 1),
        "trade_count": total_trades,
        "max_drawdown": round(max_dd, 2),
        "sharpe_proxy": round((equity/initial-1)*100 / max(max_dd, 1), 3),
    }


def run_grid_search(start_date="2025-01-01", end_date=None, top_n=20):
    if not end_date:
        end_date = str(date.today() - timedelta(days=25))

    db = SessionLocal()
    try:
        trade_dates = [r[0] for r in db.execute(text("""
            SELECT DISTINCT trade_date FROM trading_calendar
            WHERE is_open=1 AND trade_date >= :s AND trade_date <= :e ORDER BY trade_date
        """), {"s": start_date, "e": end_date}).fetchall()]

        logger.info(f"[GRID] {start_date}~{end_date} | {len(trade_dates)} 個交易日")

        # 取 0050 benchmark
        bench = db.execute(text("""
            SELECT MIN(cumulative_return), MAX(cumulative_return)
            FROM benchmark_daily_equity WHERE benchmark_code='0050'
            AND snap_date >= :s AND snap_date <= :e
        """), {"s": start_date, "e": end_date}).fetchone()
        bench_ret = float((bench[1] or 0)) - float((bench[0] or 0)) if bench else 0
        logger.info(f"[GRID] 0050 同期: {bench_ret:+.2f}%")

        # 組合參數
        keys = list(PARAM_GRID.keys())
        vals = list(PARAM_GRID.values())
        all_combos = list(product(*vals))
        logger.info(f"[GRID] 共 {len(all_combos)} 種參數組合")

        results = []
        for i, combo in enumerate(all_combos):
            params = dict(zip(keys, combo))
            r = backtest_params(db, trade_dates, params)
            if r and r["trade_count"] >= 10:
                r["params"] = params
                r["alpha"] = round(r["total_return"] - bench_ret, 2)
                results.append(r)

            if (i+1) % 100 == 0:
                logger.info(f"[GRID] 進度 {i+1}/{len(all_combos)}")

        # 排序：sharpe_proxy（報酬/最大回撤）
        results.sort(key=lambda x: -x["sharpe_proxy"])

        print("\n" + "="*70)
        print(f"Grid Search 最佳參數 Top{top_n}（排序：報酬/回撤比）")
        print("="*70)
        print(f"{'#':3} {'總報酬':8} {'Alpha':8} {'勝率':7} {'回撤':7} {'報酬/回撤':9} | 參數")
        print("-"*70)

        top = results[:top_n]
        for i, r in enumerate(top):
            p = r["params"]
            print(f"{i+1:3} {r['total_return']:+7.2f}% {r['alpha']:+7.2f}% "
                  f"{r['win_rate']:6.1f}% {r['max_drawdown']:6.1f}% {r['sharpe_proxy']:9.3f} | "
                  f"score≥{p['min_score']} pos={p['max_positions']} "
                  f"sl={p['stop_loss_pct']:.0%} tp={p['take_profit_pct']:.0%} "
                  f"hold={p['hold_days']}d")

        # 最佳參數
        best = top[0] if top else None
        if best:
            print(f"\n🏆 最佳參數組合：")
            for k, v in best["params"].items():
                print(f"   {k}: {v}")
            print(f"   → 報酬 {best['total_return']:+.2f}% | Alpha {best['alpha']:+.2f}% | "
                  f"勝率 {best['win_rate']:.1f}% | 回撤 {best['max_drawdown']:.1f}%")

        # 分析：各參數的邊際效果
        print("\n📊 參數敏感度分析（取前50名）：")
        top50 = results[:50]
        for param in ["min_score", "stop_loss_pct", "max_positions"]:
            vals_map = {}
            for r in top50:
                v = r["params"][param]
                if v not in vals_map:
                    vals_map[v] = []
                vals_map[v].append(r["total_return"])
            print(f"\n  {param}:")
            for v in sorted(vals_map.keys()):
                avg = sum(vals_map[v]) / len(vals_map[v])
                print(f"    {v}: 平均報酬 {avg:+.2f}% (n={len(vals_map[v])})")

        # 儲存
        out = {
            "benchmark_return": bench_ret,
            "total_combinations": len(all_combos),
            "valid_results": len(results),
            "top_params": top[:top_n],
            "best": best,
        }
        path = Path("data/reports/v8_grid_search.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\n✓ 完整結果：{path}")
        return out

    finally:
        db.close()


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    run_grid_search(start, top_n=top_n)
