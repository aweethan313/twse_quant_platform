"""scripts/v8_walk_forward_validation.py
Walk-Forward 策略驗證：每3個月滾動，避免過擬合
訓練期: 9個月 → 測試期: 3個月（Out-of-Sample）
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal
from loguru import logger


STRATEGIES = {
    "HighScoreTop3":    {"min_score": 80, "max_positions": 3, "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "no_chase": False},
    "HighScoreNoChase": {"min_score": 80, "max_positions": 5, "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "no_chase": True},
    "LargeCapStable":   {"min_score": 65, "max_positions": 5, "stop_loss_pct": 0.10, "take_profit_pct": 0.20, "large_cap": True},
    "ThemeSemi":        {"min_score": 65, "max_positions": 3, "stop_loss_pct": 0.10, "take_profit_pct": 0.20, "theme": "半導體"},
    "PullbackQuality":  {"min_score": 60, "max_positions": 5, "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "pullback": True},
    "CoreSatellite":    {"min_score": 65, "max_positions": 5, "stop_loss_pct": 0.10, "take_profit_pct": 0.20, "core_sat": True},
}


def get_trade_dates(db, start, end):
    return [r[0] for r in db.execute(text("""
        SELECT DISTINCT trade_date FROM trading_calendar
        WHERE is_open=1 AND trade_date >= :s AND trade_date <= :e ORDER BY trade_date
    """), {"s": start, "e": end}).fetchall()]


def simple_backtest(db, strategy_name, cfg, trade_dates):
    """簡化回測：用前瞻報酬計算策略績效"""
    if not trade_dates:
        return {"total_return": 0, "win_rate": 0, "trade_count": 0, "max_drawdown": 0}

    initial = 200000.0
    equity = initial
    peak = initial
    max_dd = 0
    wins = losses = 0

    for signal_date in trade_dates[::3]:  # 每3天調倉一次
        # 取候選股
        rows = db.execute(text("""
            SELECT cfr.code, cfr.return_5d, cfr.final_score, sm.market_cap_b
            FROM candidate_forward_returns cfr
            LEFT JOIN stock_meta sm ON sm.code=cfr.code
            WHERE cfr.signal_date=:d
              AND cfr.final_score >= :ms
              AND cfr.return_5d IS NOT NULL
            ORDER BY cfr.final_score DESC
            LIMIT :lim
        """), {"d": signal_date, "ms": cfg["min_score"],
               "lim": cfg["max_positions"] * 3}).fetchall()

        if not rows:
            continue

        # 篩選（大型股/主題/拉回）
        candidates = []
        for r in rows:
            if cfg.get("large_cap") and (not r[3] or float(r[3]) < 100):
                continue
            if cfg.get("no_chase") and float(r[2] or 0) > 85:
                continue
            candidates.append(r)

        candidates = candidates[:cfg["max_positions"]]
        if not candidates:
            continue

        # 均分倉位，計算報酬
        pos_size = equity / len(candidates)
        for r in candidates:
            ret = float(r[1] or 0) / 100
            # 停損/獲利限制
            ret = max(ret, -cfg["stop_loss_pct"])
            ret = min(ret, cfg["take_profit_pct"])
            pnl = pos_size * ret
            equity += pnl
            if pnl > 0: wins += 1
            else: losses += 1
            if equity > peak: peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd: max_dd = dd

    trade_count = wins + losses
    total_return = (equity / initial - 1) * 100
    win_rate = wins / trade_count * 100 if trade_count > 0 else 0

    return {
        "total_return": round(total_return, 2),
        "win_rate": round(win_rate, 1),
        "trade_count": trade_count,
        "max_drawdown": round(max_dd, 2),
        "final_equity": round(equity, 0),
    }


def run_walk_forward(train_months=9, test_months=3, start_year=2025):
    db = SessionLocal()
    results = []

    try:
        # 生成滾動窗口
        windows = []
        train_start = date(start_year, 1, 1)
        while True:
            train_end   = train_start + timedelta(days=train_months*30)
            test_start  = train_end + timedelta(days=1)
            test_end    = test_start + timedelta(days=test_months*30)
            if test_end > date.today():
                break
            windows.append({
                "train": (str(train_start), str(train_end)),
                "test":  (str(test_start),  str(test_end)),
            })
            train_start = train_start + timedelta(days=test_months*30)
            if len(windows) >= 4:
                break

        logger.info(f"[WF] Walk-Forward: {len(windows)} 個窗口")

        for wi, window in enumerate(windows):
            logger.info(f"\n[WF] 窗口 {wi+1}: 訓練 {window['train']} | 測試 {window['test']}")

            # 取 0050 benchmark
            bench_rows = db.execute(text("""
                SELECT MIN(cumulative_return), MAX(cumulative_return)
                FROM benchmark_daily_equity
                WHERE benchmark_code='0050'
                  AND snap_date >= :s AND snap_date <= :e
            """), {"s": window["test"][0], "e": window["test"][1]}).fetchone()
            bench_ret = float((bench_rows[1] or 0)) - float((bench_rows[0] or 0)) if bench_rows else 0

            test_dates = get_trade_dates(db, window["test"][0], window["test"][1])
            window_results = {"window": wi+1, "period": window["test"], "benchmark": bench_ret, "strategies": []}

            for sname, cfg in STRATEGIES.items():
                r = simple_backtest(db, sname, cfg, test_dates)
                alpha = r["total_return"] - bench_ret
                beat = "✅" if alpha > 0 else "❌"
                logger.info(f"  {beat} {sname:20} 報酬={r['total_return']:+.2f}% alpha={alpha:+.2f}% 勝率={r['win_rate']:.1f}%")
                window_results["strategies"].append({
                    "strategy": sname, **r,
                    "alpha": round(alpha, 2),
                    "beat_benchmark": alpha > 0
                })

            results.append(window_results)

        # 彙總：哪些策略在 OOS 持續跑贏
        print("\n" + "="*60)
        print("Walk-Forward OOS 彙總")
        print("="*60)
        strategy_scores = {s: {"wins": 0, "total": 0, "avg_alpha": []} for s in STRATEGIES}
        for window in results:
            for sr in window["strategies"]:
                s = sr["strategy"]
                strategy_scores[s]["total"] += 1
                if sr["beat_benchmark"]:
                    strategy_scores[s]["wins"] += 1
                strategy_scores[s]["avg_alpha"].append(sr["alpha"])

        summary = []
        for sname, sc in strategy_scores.items():
            if sc["total"] == 0: continue
            win_pct = sc["wins"] / sc["total"] * 100
            avg_alpha = sum(sc["avg_alpha"]) / len(sc["avg_alpha"])
            rec = "⭐ STRONG" if win_pct >= 75 and avg_alpha > 2 else \
                  "✅ KEEP"   if win_pct >= 50 else \
                  "⚠️ WEAK"  if win_pct >= 25 else "❌ DROP"
            summary.append((sname, win_pct, avg_alpha, rec))
            print(f"  {rec:12} {sname:20} OOS勝率={win_pct:.0f}% 平均Alpha={avg_alpha:+.2f}%")

        # 儲存結果
        out = {"windows": results, "summary": [
            {"strategy": s, "oos_win_pct": wp, "avg_alpha": aa, "recommendation": rec}
            for s, wp, aa, rec in summary
        ]}
        path = Path("data/reports/v8_walk_forward.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\n✓ 報告：{path}")
        return out

    finally:
        db.close()


if __name__ == "__main__":
    run_walk_forward()
