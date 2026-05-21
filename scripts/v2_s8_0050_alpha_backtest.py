from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import math
from collections import defaultdict, deque

from sqlalchemy import text
from backend.models.database import SessionLocal


INITIAL_CASH = 200_000.0
BENCHMARK_CODE = "0050"

MAX_POSITIONS = 6
INITIAL_POSITION_PCT = 0.20
MAX_POSITION_PCT = 0.30
MIN_CASH_PCT = 0.02

BUY_COMPOSITE_MIN = 60.0
BUY_MOMENTUM_MIN = 60.0
BUY_CHIP_MIN = 45.0
BUY_FUNDAMENTAL_MIN = 40.0
MIN_AVG_VOLUME_SHARES = 1_000_000  # 1000 張

SELL_COMPOSITE_WEAK = 50.0
SELL_MOMENTUM_WEAK = 45.0

STOP_LOSS_PCT = -0.06
TRAILING_START_PCT = 0.12
TRAILING_DROP_PCT = 0.05

FEE_RATE = 0.001425 * 0.28
TAX_RATE = 0.003
SLIPPAGE_RATE = 0.001


@dataclass
class Position:
    code: str
    shares: int
    avg_cost: float
    buy_date: str
    highest_price: float


def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def buy_cost(amount):
    return amount * (FEE_RATE + SLIPPAGE_RATE)


def sell_cost(amount):
    return amount * (FEE_RATE + TAX_RATE + SLIPPAGE_RATE)


def load_ohlcv(db):
    rows = db.execute(text("""
        SELECT code, trade_date, open, high, low, close, volume
        FROM ohlcv_daily
        WHERE open IS NOT NULL
          AND high IS NOT NULL
          AND low IS NOT NULL
          AND close IS NOT NULL
          AND volume IS NOT NULL
          AND volume > 0
        ORDER BY trade_date, code
    """)).fetchall()

    by_date = defaultdict(dict)
    dates = set()

    for r in rows:
        code = str(r[0])
        d = str(r[1])
        dates.add(d)
        by_date[d][code] = {
            "open": safe_float(r[2]),
            "high": safe_float(r[3]),
            "low": safe_float(r[4]),
            "close": safe_float(r[5]),
            "volume": safe_float(r[6]),
        }

    return sorted(dates), dict(by_date)


def load_scores(db):
    rows = db.execute(text("""
        SELECT
            code,
            score_date,
            composite_score,
            momentum_score,
            chip_score,
            fundamental_score,
            valuation_score,
            macro_score,
            news_score,
            signal
        FROM daily_scores
        ORDER BY score_date, code
    """)).fetchall()

    by_date = defaultdict(dict)

    for r in rows:
        code = str(r[0])
        d = str(r[1])
        by_date[d][code] = {
            "composite": safe_float(r[2]),
            "momentum": safe_float(r[3]),
            "chip": safe_float(r[4]),
            "fundamental": safe_float(r[5]),
            "valuation": safe_float(r[6]),
            "macro": safe_float(r[7]),
            "news": safe_float(r[8]),
            "signal": str(r[9] or ""),
        }

    return dict(by_date)


def build_features(dates, by_date):
    """
    不偷看未來：
    每一天的 MA20 / avg_volume20 只使用該日與該日以前資料。
    """
    close_window = defaultdict(lambda: deque(maxlen=20))
    volume_window = defaultdict(lambda: deque(maxlen=20))
    features = defaultdict(dict)

    for d in dates:
        for code, p in by_date.get(d, {}).items():
            cw = close_window[code]
            vw = volume_window[code]

            cw.append(p["close"])
            vw.append(p["volume"])

            if len(cw) >= 20:
                features[d][code] = {
                    "ma20": sum(cw) / len(cw),
                    "avg_volume20": sum(vw) / len(vw),
                }

    return dict(features)


def total_asset(cash, positions, price_map):
    total = cash
    for code, pos in positions.items():
        p = price_map.get(code)
        if p:
            total += pos.shares * p["close"]
    return total


def choose_candidates(decision_date, scores_by_date, by_date, features):
    """
    decision_date 收盤後選股，隔天才買。
    """
    score_map = scores_by_date.get(decision_date, {})
    price_map = by_date.get(decision_date, {})
    feature_map = features.get(decision_date, {})

    candidates = []

    for code, s in score_map.items():
        if code == BENCHMARK_CODE:
            continue
        if code not in price_map or code not in feature_map:
            continue

        p = price_map[code]
        f = feature_map[code]

        if p["close"] <= f["ma20"]:
            continue
        if f["avg_volume20"] < MIN_AVG_VOLUME_SHARES:
            continue
        if s["signal"] != "BUY":
            continue
        if s["composite"] < BUY_COMPOSITE_MIN:
            continue
        if s["momentum"] < BUY_MOMENTUM_MIN:
            continue
        if s["chip"] < BUY_CHIP_MIN:
            continue
        if s["fundamental"] < BUY_FUNDAMENTAL_MIN:
            continue

        alpha_score = (
            0.35 * s["composite"]
            + 0.25 * s["momentum"]
            + 0.15 * s["chip"]
            + 0.10 * s["fundamental"]
            + 0.10 * s["news"]
            + 0.05 * s["macro"]
        )

        candidates.append((code, alpha_score, s["composite"], s["momentum"]))

    candidates.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
    return [x[0] for x in candidates[:20]]


def sell_decision(code, pos, decision_date, score_map, price_map, feature_map):
    """
    decision_date 收盤後判斷是否賣出，隔天開盤執行。
    """
    if code not in price_map:
        return False, "NO_PRICE"

    p = price_map[code]
    close = p["close"]

    if close > pos.highest_price:
        pos.highest_price = close

    ret = close / pos.avg_cost - 1.0

    if ret <= STOP_LOSS_PCT:
        return True, "SELL_STOP_LOSS"

    high_ret = pos.highest_price / pos.avg_cost - 1.0
    drop_from_high = close / pos.highest_price - 1.0 if pos.highest_price > 0 else 0.0

    if high_ret >= TRAILING_START_PCT and drop_from_high <= -TRAILING_DROP_PCT:
        return True, "SELL_TRAILING_STOP"

    s = score_map.get(code)
    if s:
        if s["composite"] < SELL_COMPOSITE_WEAK or s["momentum"] < SELL_MOMENTUM_WEAK:
            return True, "SELL_SCORE_WEAKEN"

    f = feature_map.get(code)
    if f and close < f["ma20"]:
        return True, "SELL_TREND_BREAK_MA20"

    return False, "HOLD"


def benchmark_stats(dates, by_date):
    """
    0050 benchmark 使用清理後報酬序列。

    原因：
    DB 裡 0050 有明顯異常跳價，例如單日 +80%、-50%。
    這通常是除權息 / 分割 / 不同資料口徑混在一起。
    對 0050 這種 ETF，單日絕對報酬 >= 15% 幾乎一定不適合直接當 benchmark return。
    """
    vals = []
    for d in dates:
        p = by_date.get(d, {}).get(BENCHMARK_CODE)
        if p and p["close"] > 0:
            vals.append((d, p["close"]))

    if len(vals) < 2:
        return 0.0, 0.0, []

    abnormal_rows = []
    adjusted_values = []
    adjusted_index = 100.0
    adjusted_values.append(adjusted_index)

    for i in range(1, len(vals)):
        d, close = vals[i]
        prev_d, prev_close = vals[i - 1]

        raw_ret = close / prev_close - 1.0 if prev_close > 0 else 0.0

        if abs(raw_ret) >= 0.15:
            abnormal_rows.append({
                "date": d,
                "prev_close": round(prev_close, 4),
                "close": round(close, 4),
                "raw_return_pct": round(raw_ret * 100, 2),
            })
            # 異常跳價不納入 benchmark 報酬，避免除權息 / 分割 / 錯誤資料扭曲績效
            clean_ret = 0.0
        else:
            clean_ret = raw_ret

        adjusted_index *= (1.0 + clean_ret)
        adjusted_values.append(adjusted_index)

    ret = adjusted_values[-1] / adjusted_values[0] - 1.0

    peak = adjusted_values[0]
    max_dd = 0.0
    for v in adjusted_values:
        peak = max(peak, v)
        dd = v / peak - 1.0
        max_dd = min(max_dd, dd)

    return ret, max_dd, abnormal_rows


def main():
    out_dir = Path("data/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    dates, by_date = load_ohlcv(db)
    scores_by_date = load_scores(db)
    db.close()

    print(f"loaded trading dates: {len(dates)}")
    print(f"loaded score dates: {len(scores_by_date)}")
    print("building rolling features...")
    features = build_features(dates, by_date)
    print("features ready.")

    if len(dates) < 80:
        print("交易日資料不足，無法回測。")
        return

    start_idx = max(30, len(dates) - 250)
    exec_indices = list(range(start_idx, len(dates)))

    cash = INITIAL_CASH
    positions = {}

    equity_rows = []
    trade_rows = []

    for step, idx in enumerate(exec_indices, start=1):
        today = dates[idx]
        decision_date = dates[idx - 1]

        today_prices = by_date.get(today, {})
        decision_prices = by_date.get(decision_date, {})
        decision_scores = scores_by_date.get(decision_date, {})
        decision_features = features.get(decision_date, {})

        if step % 25 == 1:
            print(f"[{step}/{len(exec_indices)}] {today} positions={len(positions)} cash={cash:,.0f}")

        # 1. 根據昨天收盤資訊，今天開盤賣出
        for code in list(positions.keys()):
            pos = positions[code]

            should_sell, reason = sell_decision(
                code=code,
                pos=pos,
                decision_date=decision_date,
                score_map=decision_scores,
                price_map=decision_prices,
                feature_map=decision_features,
            )

            if not should_sell:
                continue
            if code not in today_prices:
                continue

            # 禁止同日買賣：若今天才買，不可能在這裡賣；保險仍保留
            if pos.buy_date == today:
                continue

            sell_price = today_prices[code]["open"]
            gross = pos.shares * sell_price
            cost = sell_cost(gross)
            cash += gross - cost

            pnl = (sell_price - pos.avg_cost) * pos.shares - cost
            ret = sell_price / pos.avg_cost - 1.0

            trade_rows.append({
                "date": today,
                "decision_date": decision_date,
                "action": "SELL",
                "code": code,
                "shares": pos.shares,
                "price": round(sell_price, 4),
                "amount": round(gross, 2),
                "cost": round(cost, 2),
                "pnl": round(pnl, 2),
                "return_pct": round(ret * 100, 2),
                "reason": reason,
            })

            del positions[code]

        # 2. 根據昨天收盤選股，今天開盤買入
        current_asset = total_asset(cash, positions, today_prices)
        min_cash = current_asset * MIN_CASH_PCT

        if len(positions) < MAX_POSITIONS and cash > min_cash:
            candidates = choose_candidates(decision_date, scores_by_date, by_date, features)

            for code in candidates:
                if len(positions) >= MAX_POSITIONS:
                    break
                if code in positions:
                    continue
                if code not in today_prices:
                    continue

                buy_price = today_prices[code]["open"]
                if buy_price <= 0:
                    continue

                current_asset = total_asset(cash, positions, today_prices)
                min_cash = current_asset * MIN_CASH_PCT
                usable_cash = max(0.0, cash - min_cash)

                target_amount = current_asset * INITIAL_POSITION_PCT
                max_amount = current_asset * MAX_POSITION_PCT
                buy_amount = min(target_amount, max_amount, usable_cash)

                if buy_amount < buy_price:
                    continue

                shares = int(buy_amount // buy_price)
                if shares <= 0:
                    continue

                gross = shares * buy_price
                cost = buy_cost(gross)
                total_pay = gross + cost

                if total_pay > cash - min_cash:
                    continue

                cash -= total_pay
                positions[code] = Position(
                    code=code,
                    shares=shares,
                    avg_cost=buy_price,
                    buy_date=today,
                    highest_price=buy_price,
                )

                s = decision_scores.get(code, {})
                trade_rows.append({
                    "date": today,
                    "decision_date": decision_date,
                    "action": "BUY",
                    "code": code,
                    "shares": shares,
                    "price": round(buy_price, 4),
                    "amount": round(gross, 2),
                    "cost": round(cost, 2),
                    "pnl": 0.0,
                    "return_pct": 0.0,
                    "reason": (
                        f"S8_ALPHA_BUY "
                        f"composite={safe_float(s.get('composite')):.2f} "
                        f"momentum={safe_float(s.get('momentum')):.2f}"
                    ),
                })

        day_asset = total_asset(cash, positions, today_prices)
        equity_rows.append({
            "date": today,
            "cash": round(cash, 2),
            "position_count": len(positions),
            "total_asset": round(day_asset, 2),
        })

    if not equity_rows:
        print("沒有產生 equity rows。")
        return

    strategy_ret = equity_rows[-1]["total_asset"] / INITIAL_CASH - 1.0

    peak = INITIAL_CASH
    max_dd = 0.0
    for r in equity_rows:
        v = r["total_asset"]
        peak = max(peak, v)
        dd = v / peak - 1.0
        max_dd = min(max_dd, dd)

    backtest_dates = [r["date"] for r in equity_rows]
    bench_ret, bench_dd, bench_bad_rows = benchmark_stats(backtest_dates, by_date)
    alpha = strategy_ret - bench_ret

    benchmark_bad_path = out_dir / "s8_0050_benchmark_abnormal_rows.csv"
    with benchmark_bad_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["date", "prev_close", "close", "raw_return_pct"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(bench_bad_rows)

    trades_path = out_dir / "s8_0050_alpha_trades.csv"
    equity_path = out_dir / "s8_0050_alpha_equity.csv"
    report_path = out_dir / "s8_0050_alpha_report.md"

    with trades_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "date", "decision_date", "action", "code", "shares", "price",
            "amount", "cost", "pnl", "return_pct", "reason"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trade_rows)

    with equity_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["date", "cash", "position_count", "total_asset"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(equity_rows)

    buys = [r for r in trade_rows if r["action"] == "BUY"]
    sells = [r for r in trade_rows if r["action"] == "SELL"]
    realized_pnl = sum(safe_float(r["pnl"]) for r in sells)
    wins = [r for r in sells if safe_float(r["pnl"]) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0.0

    report = f"""# S8：0050 Alpha Strategy v1.3 aggressive 回測報告

## 回測設定

- 不偷看未來：使用前一交易日收盤後資訊，隔日開盤執行
- 初始資金：{INITIAL_CASH:,.0f}
- Benchmark：0050（清理單日異常跳價 >= 15%）
- Benchmark 異常跳價筆數：{len(bench_bad_rows)}
- start：{backtest_dates[0]}
- end：{backtest_dates[-1]}

## 績效比較

| 指標 | S8 | 0050 |
|---|---:|---:|
| 報酬率 | {strategy_ret * 100:.2f}% | {bench_ret * 100:.2f}% |
| 最大回撤 | {max_dd * 100:.2f}% | {bench_dd * 100:.2f}% |

## 交易統計

| 指標 | 數值 |
|---|---:|
| 期末資產 | {equity_rows[-1]["total_asset"]:,.2f} |
| Alpha | {alpha * 100:.2f}% |
| 買進次數 | {len(buys)} |
| 賣出次數 | {len(sells)} |
| 勝率 | {win_rate:.2f}% |
| 已實現損益 | {realized_pnl:,.2f} |

## 判斷

"""

    if alpha > 0 and max_dd >= bench_dd:
        report += "- S8 v1.3 aggressive 暫時跑贏 0050，且最大回撤沒有比 0050 更差。\n"
    elif alpha > 0:
        report += "- S8 v1.3 aggressive 有跑贏 0050，但最大回撤較差，仍需改善風控。\n"
    else:
        report += "- S8 v1.3 aggressive 尚未跑贏 0050，需要調整選股門檻、進場或出場規則。\n"

    report += f"""
## 輸出檔案

- `{trades_path}`
- `{equity_path}`
- `{report_path}`
- `{benchmark_bad_path}`
"""

    report_path.write_text(report, encoding="utf-8")

    print()
    print("S8 0050 Alpha backtest finished.")
    print(f"Report: {report_path}")
    print(f"Trades: {trades_path}")
    print(f"Equity: {equity_path}")
    print()
    print(f"Strategy return: {strategy_ret * 100:.2f}%")
    print(f"0050 return: {bench_ret * 100:.2f}%")
    print(f"Alpha: {alpha * 100:.2f}%")
    print(f"Strategy max drawdown: {max_dd * 100:.2f}%")
    print(f"0050 max drawdown: {bench_dd * 100:.2f}%")


if __name__ == "__main__":
    main()
