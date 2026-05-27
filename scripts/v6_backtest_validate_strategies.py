"""scripts/v6_backtest_validate_strategies.py
V6 歷史回測驗證器 - 2025/1/1 到最新資料日
用 daily_scores + technical_daily_features 模擬各策略邏輯，不偷看未來
"""
import sys, os, argparse, json, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

FEE_RATE  = 0.001425 * 0.38
TAX_RATE  = 0.003
SLIP_BUY  = 0.002
SLIP_SELL = 0.003
MIN_FEE   = 20
INIT_CASH = 200000.0


def get_candidates(db, signal_date: str, cfg: dict) -> list:
    """取候選股（和 V5 決策引擎邏輯一致）"""
    min_score = cfg.get("min_score", 65)
    max_rsi   = cfg.get("max_rsi14", 80)
    min_rsi   = cfg.get("min_rsi14", 30)
    max_ma    = cfg.get("max_distance_ma20_pct", 12)
    large_cap = cfg.get("large_cap_only", 0)
    theme     = cfg.get("theme_filter")

    where = [
        "ds.score_date=:sd",
        "ds.final_action IN ('BUY','WATCH')",
        "o.close IS NOT NULL AND o.close >= 10",
        f"ds.final_score >= {min_score}",
        f"(tdf.rsi14 IS NULL OR (tdf.rsi14 >= {min_rsi} AND tdf.rsi14 < {max_rsi}))",
        f"(tdf.distance_ma20 IS NULL OR ABS(tdf.distance_ma20) < {max_ma})",
        "(tdf.return_5d IS NULL OR tdf.return_5d < 12)",
    ]
    if large_cap:
        where.append("ds.stock_class IN ('CORE_LARGE_CAP','LARGE_LIQUID')")
    else:
        where.append("ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','NORMAL')")

    theme_sql = ""
    if theme:
        themes = [t.strip() for t in theme.split(",")]
        ph = ",".join(f"'{t}'" for t in themes)
        where.append(f"""ds.code IN (
            SELECT ds2.code FROM daily_scores ds2
            WHERE ds2.score_date=:sd AND EXISTS (
                SELECT 1 FROM theme_trend_daily ttd
                WHERE ttd.theme IN ({ph}) AND ttd.context_date=:sd
                AND ttd.leader_codes LIKE '%'||ds2.code||'%'
            ))""")

    rows = db.execute(text(f"""
        SELECT ds.code, sm.name, ds.final_score, tdf.rsi14, o.close
        FROM daily_scores ds
        LEFT JOIN stock_meta sm ON sm.code=ds.code
        LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code AND tdf.trade_date=:sd
        LEFT JOIN ohlcv_daily o ON o.code=ds.code AND o.trade_date=:sd
        WHERE {' AND '.join(where)}
        ORDER BY CASE ds.stock_class WHEN 'CORE_LARGE_CAP' THEN 1 WHEN 'LARGE_LIQUID' THEN 2 ELSE 3 END,
                 ds.final_score DESC
        LIMIT 20
    """), {"sd": signal_date}).fetchall()

    return [{"code": r[0], "name": r[1], "score": float(r[2] or 0), "rsi": r[3], "close": float(r[4] or 0)} for r in rows]


def run_strategy_backtest(db, strategy_name: str, cfg: dict, trade_dates: list) -> dict:
    """單一策略回測"""
    cash = INIT_CASH
    positions = {}  # code -> {lots, avg_cost, buy_date}
    fills = []
    sl_pct = cfg.get("stop_loss_pct", 0.08)
    tp_pct = cfg.get("take_profit_pct", 0.15)
    max_pos = cfg.get("max_positions", 5)
    max_pct = cfg.get("max_position_pct", 0.20)
    rank_limit = cfg.get("candidate_rank_limit", 5)

    equity_curve = []
    total_fee = total_tax = 0

    for i, (signal_date,) in enumerate(trade_dates[:-1]):
        next_date = trade_dates[i+1][0]

        # 1. 停損停利檢查（用 signal_date 收盤）
        to_sell = []
        for code, pos in list(positions.items()):
            price_row = db.execute(text(
                "SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d"
            ), {"c": code, "d": str(signal_date)}).fetchone()
            if not price_row or price_row[0] is None: continue
            cur_price = float(price_row[0])
            pnl_pct = (cur_price / pos["avg_cost"] - 1)
            if pnl_pct <= -sl_pct or pnl_pct >= tp_pct:
                to_sell.append((code, cur_price, "sl" if pnl_pct <= -sl_pct else "tp"))

        # 執行賣出（next_date 開盤）
        for code, ref_price, reason in to_sell:
            price_row = db.execute(text(
                "SELECT open, close FROM ohlcv_daily WHERE code=:c AND trade_date=:d"
            ), {"c": code, "d": str(next_date)}).fetchone()
            if not price_row: continue
            sell_price = float(price_row[0] or price_row[1]) * (1 - SLIP_SELL)
            pos = positions[code]
            gross = sell_price * pos["lots"]
            fee = max(MIN_FEE, gross * FEE_RATE)
            tax = gross * TAX_RATE
            net = gross - fee - tax
            pnl = net - pos["avg_cost"] * pos["lots"]
            cash += net
            total_fee += fee; total_tax += tax
            fills.append({"date": str(next_date), "code": code, "action": "SELL",
                          "price": sell_price, "lots": pos["lots"], "pnl": pnl, "reason": reason})
            del positions[code]

        # 2. 產生買入訊號
        candidates = get_candidates(db, str(signal_date), cfg)[:rank_limit]
        for rank, cand in enumerate(candidates):
            code = cand["code"]
            if code in positions: continue
            if len(positions) >= max_pos: break
            ref_price = cand["close"]
            if ref_price <= 0: continue
            max_amount = min(cash * max_pct, cash - 1000)
            if max_amount < ref_price * 10: continue

            # 執行買入（next_date 開盤）
            price_row = db.execute(text(
                "SELECT open, close FROM ohlcv_daily WHERE code=:c AND trade_date=:d"
            ), {"c": code, "d": str(next_date)}).fetchone()
            if not price_row: continue
            buy_price = float(price_row[0] or price_row[1]) * (1 + SLIP_BUY)
            shares = int(max_amount / buy_price)
            if shares <= 0: continue
            gross = buy_price * shares
            fee = max(MIN_FEE, gross * FEE_RATE)
            total_cost = gross + fee
            if total_cost > cash: continue
            cash -= total_cost
            total_fee += fee
            positions[code] = {"lots": shares, "avg_cost": buy_price, "buy_date": str(next_date)}
            fills.append({"date": str(next_date), "code": code, "action": "BUY",
                          "price": buy_price, "lots": shares, "pnl": 0})

        # 3. 計算當日市值
        mkt = 0
        for code, pos in positions.items():
            p = db.execute(text(
                "SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d"
            ), {"c": code, "d": str(signal_date)}).scalar()
            if p: mkt += float(p) * pos["lots"]
        equity_curve.append({"date": str(signal_date), "equity": cash + mkt})

    # 最終清算
    final_equity = cash
    for code, pos in positions.items():
        last_price = db.execute(text(
            "SELECT close FROM ohlcv_daily WHERE code=:c ORDER BY trade_date DESC LIMIT 1"
        ), {"c": code}).scalar()
        if last_price:
            final_equity += float(last_price) * pos["lots"]

    # 統計
    total_return = (final_equity / INIT_CASH - 1) * 100
    buy_fills = [f for f in fills if f["action"] == "BUY"]
    sell_fills = [f for f in fills if f["action"] == "SELL"]
    wins = [f for f in sell_fills if f["pnl"] > 0]
    losses = [f for f in sell_fills if f["pnl"] < 0]
    win_rate = len(wins) / len(sell_fills) * 100 if sell_fills else 0
    avg_win = sum(f["pnl"] for f in wins) / len(wins) if wins else 0
    avg_loss = sum(f["pnl"] for f in losses) / len(losses) if losses else 0
    profit_factor = sum(f["pnl"] for f in wins) / abs(sum(f["pnl"] for f in losses)) if losses else 99.0

    # 最大回撤
    peak = INIT_CASH
    max_dd = 0
    for e in equity_curve:
        eq = e["equity"]
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd: max_dd = dd

    # 年化報酬
    n_days = len(equity_curve)
    ann_ret = ((final_equity / INIT_CASH) ** (252 / max(n_days, 1)) - 1) * 100 if n_days > 0 else 0

    return {
        "strategy_name": strategy_name,
        "total_return": round(total_return, 3),
        "annualized_return": round(ann_ret, 3),
        "max_drawdown": round(max_dd, 3),
        "win_rate": round(win_rate, 1),
        "trade_count": len(sell_fills),
        "profit_factor": round(min(profit_factor, 99.0), 3),
        "average_win": round(avg_win, 0),
        "average_loss": round(avg_loss, 0),
        "fee_total": round(total_fee, 0),
        "tax_total": round(total_tax, 0),
        "final_equity": round(final_equity, 0),
        "equity_curve": equity_curve,
    }


def run(start_date="2025-01-01", end_date=None):
    db = SessionLocal()
    try:
        if not end_date or end_date == "latest":
            end_date = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()

        print(f"\n=== V6 歷史回測驗證 {start_date} ~ {end_date} ===\n")

        trade_dates = db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date >= :s AND trade_date <= :e
            ORDER BY trade_date
        """), {"s": start_date, "e": end_date}).fetchall()
        print(f"回測交易日：{len(trade_dates)} 天\n")

        # 取 V5 策略設定
        configs = db.execute(text("""
            SELECT a.id, a.name, cfg.strategy_name, cfg.min_score, cfg.max_positions,
                   cfg.max_position_pct, cfg.stop_loss_pct, cfg.take_profit_pct,
                   cfg.large_cap_only, cfg.no_chase_enabled,
                   cfg.max_rsi14, cfg.min_rsi14, cfg.max_distance_ma20_pct,
                   cfg.theme_filter, cfg.candidate_rank_limit
            FROM strategy_accounts a
            JOIN strategy_account_configs cfg ON cfg.account_id=a.id
            WHERE a.id >= 11 ORDER BY a.id
        """)).fetchall()

        # 0050 benchmark
        bench_rows = db.execute(text("""
            SELECT snap_date, cumulative_return FROM benchmark_daily_equity
            WHERE benchmark_code='0050' AND snap_date >= :s AND snap_date <= :e
            ORDER BY snap_date
        """), {"s": start_date, "e": end_date}).fetchall()
        bench_final = float(bench_rows[-1][1]) if bench_rows else 0

        results = []
        for cfg_row in configs:
            aid, aname, sname = cfg_row[0], cfg_row[1], cfg_row[2]
            cfg = {
                "min_score": float(cfg_row[3] or 65),
                "max_positions": int(cfg_row[4] or 5),
                "max_position_pct": float(cfg_row[5] or 0.20),
                "stop_loss_pct": float(cfg_row[6] or 0.08),
                "take_profit_pct": float(cfg_row[7] or 0.15),
                "large_cap_only": int(cfg_row[8] or 0),
                "no_chase_enabled": int(cfg_row[9] or 0),
                "max_rsi14": float(cfg_row[10] or 80),
                "min_rsi14": float(cfg_row[11] or 30),
                "max_distance_ma20_pct": float(cfg_row[12] or 12),
                "theme_filter": cfg_row[13],
                "candidate_rank_limit": int(cfg_row[14] or 5),
            }
            print(f"回測 {aname}...")
            r = run_strategy_backtest(db, sname, cfg, trade_dates)
            r["account_id"] = aid
            r["account_name"] = aname
            r["benchmark_0050_return"] = round(bench_final, 3)
            r["alpha_vs_0050"] = round(r["total_return"] - bench_final, 3)
            r["start_date"] = start_date
            r["end_date"] = str(end_date)
            results.append(r)

            # 寫入 DB
            db.execute(text("""
                INSERT INTO v6_strategy_backtest_results
                    (strategy_name, start_date, end_date,
                     total_return, benchmark_0050_return, alpha_vs_0050,
                     annualized_return, max_drawdown, win_rate,
                     trade_count, profit_factor, average_win, average_loss,
                     fee_total, tax_total)
                VALUES (:sn,:sd,:ed,:tr,:br,:al,:ar,:md,:wr,:tc,:pf,:aw,:al2,:ft,:tt)
            """), {
                "sn": sname, "sd": start_date, "ed": str(end_date),
                "tr": r["total_return"], "br": r["benchmark_0050_return"],
                "al": r["alpha_vs_0050"], "ar": r["annualized_return"],
                "md": r["max_drawdown"], "wr": r["win_rate"],
                "tc": r["trade_count"], "pf": r["profit_factor"],
                "aw": r["average_win"], "al2": r["average_loss"],
                "ft": r["fee_total"], "tt": r["tax_total"],
            })
            db.commit()
            print(f"  → 報酬={r['total_return']:+.2f}% alpha={r['alpha_vs_0050']:+.2f}% 回撤={r['max_drawdown']:.1f}% 勝率={r['win_rate']:.1f}%")

        # 輸出報告
        print(f"\n=== 回測結果 vs 0050（{bench_final:+.2f}%）===")
        print(f"{'策略':25} {'報酬':8} {'Alpha':8} {'回撤':7} {'勝率':6} {'交易':5}")
        for r in sorted(results, key=lambda x: x["alpha_vs_0050"], reverse=True):
            beat = "✅" if r["alpha_vs_0050"] > 0 else "❌"
            print(f"  {beat} {r['account_name']:22} {r['total_return']:+7.2f}% {r['alpha_vs_0050']:+7.2f}% {r['max_drawdown']:6.1f}% {r['win_rate']:5.1f}% {r['trade_count']:4}筆")

        # 存 JSON
        path = Path(f"data/reports/v6_backtest_{start_date}_{end_date}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2,
                                   default=lambda x: str(x)))
        print(f"\n✓ 結果儲存：{path}")
        return results

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="latest")
    args = parser.parse_args()
    run(args.start_date, args.end_date)
