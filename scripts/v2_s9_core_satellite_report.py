from __future__ import annotations

from pathlib import Path
from collections import defaultdict
import csv
import math

from sqlalchemy import text
from backend.models.database import SessionLocal


INITIAL_ASSET = 200_000.0
BENCHMARK_CODE = "0050"
ABNORMAL_RETURN_THRESHOLD = 0.15


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


def read_csv_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_s8_equity():
    path = Path("data/reports/s8_0050_alpha_equity.csv")
    if not path.exists():
        raise FileNotFoundError(
            "找不到 data/reports/s8_0050_alpha_equity.csv，請先跑 S8 backtest"
        )

    rows = read_csv_rows(path)
    out = []

    for r in rows:
        d = str(r["date"])
        v = safe_float(r["total_asset"])
        if v > 0:
            out.append((d, v))

    if len(out) < 2:
        raise ValueError("S8 equity rows 太少，無法做 S9 回測")

    return out


def load_regime_map():
    path = Path("data/reports/s8_regime_audit.csv")
    if not path.exists():
        raise FileNotFoundError(
            "找不到 data/reports/s8_regime_audit.csv，請先跑 scripts.v2_s8_regime_audit"
        )

    rows = read_csv_rows(path)
    out = {}

    for r in rows:
        out[str(r["date"])] = {
            "mode": str(r["regime_mode"]),
            "score": safe_float(r.get("regime_score")),
            "breadth": safe_float(r.get("breadth")),
            "reason": str(r.get("reason") or ""),
        }

    return out


def load_0050_close_map(start_date: str, end_date: str):
    db = SessionLocal()

    rows = db.execute(text("""
        SELECT trade_date, close
        FROM ohlcv_daily
        WHERE code = :code
          AND trade_date >= :start
          AND trade_date <= :end
          AND close IS NOT NULL
        ORDER BY trade_date
    """), {
        "code": BENCHMARK_CODE,
        "start": start_date,
        "end": end_date,
    }).fetchall()

    db.close()

    return {str(r[0]): safe_float(r[1]) for r in rows}


def build_clean_0050_returns(dates, close_map):
    """
    因為目前 DB 的 0050 價格有異常跳價，所以 benchmark 先用 clean return：
    若 0050 單日漲跌幅 >= 15%，視為資料異常，該日報酬設為 0。
    之後如果修好官方 0050 資料，再改回原始報酬。
    """
    returns = {}
    abnormal = []

    prev_close = None
    prev_date = None

    for d in dates:
        close = close_map.get(d)

        if close is None or close <= 0:
            returns[d] = 0.0
            continue

        if prev_close is None or prev_close <= 0:
            returns[d] = 0.0
            prev_close = close
            prev_date = d
            continue

        raw_ret = close / prev_close - 1.0

        if abs(raw_ret) >= ABNORMAL_RETURN_THRESHOLD:
            returns[d] = 0.0
            abnormal.append({
                "date": d,
                "prev_date": prev_date,
                "prev_close": round(prev_close, 4),
                "close": round(close, 4),
                "raw_return_pct": round(raw_ret * 100, 2),
            })
        else:
            returns[d] = raw_ret

        prev_close = close
        prev_date = d

    return returns, abnormal


def max_drawdown(values):
    peak = values[0]
    max_dd = 0.0

    for v in values:
        peak = max(peak, v)
        dd = v / peak - 1.0 if peak > 0 else 0.0
        max_dd = min(max_dd, dd)

    return max_dd


def portfolio_metrics(values):
    ret = values[-1] / INITIAL_ASSET - 1.0
    dd = max_drawdown(values)
    return ret, dd, values[-1]


def regime_weights(mode: str):
    """
    原始 S9 adaptive 配置。
    """
    if mode == "OFFENSIVE":
        return 0.50, 0.45, 0.05
    if mode == "DEFENSIVE":
        return 0.60, 0.10, 0.30
    return 0.55, 0.30, 0.15


def optimized_regime_weights(mode: str):
    """
    S9 Optimized Core-Satellite v1.

    來自 scripts.v2_s9_weight_optimizer 的最佳風控型結果：
    - OFFENSIVE: 100% 0050
    - NEUTRAL: 80% 0050, 10% S8, 10% cash
    - DEFENSIVE: 40% 0050, 10% S8, 50% cash
    """
    if mode == "OFFENSIVE":
        return 1.00, 0.00, 0.00
    if mode == "DEFENSIVE":
        return 0.40, 0.10, 0.50
    return 0.80, 0.10, 0.10


def main():
    out_dir = Path("data/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    s8_equity = load_s8_equity()
    regime_map = load_regime_map()

    dates = [x[0] for x in s8_equity]
    s8_asset_raw = {d: v for d, v in s8_equity}

    close_map = load_0050_close_map(dates[0], dates[-1])
    ret_0050, abnormal_rows = build_clean_0050_returns(dates, close_map)

    value_0050 = INITIAL_ASSET
    value_s8 = INITIAL_ASSET
    value_s9_fixed = INITIAL_ASSET
    value_s9_adaptive = INITIAL_ASSET
    value_s9_optimized = INITIAL_ASSET

    values_0050 = []
    values_s8 = []
    values_s9_fixed = []
    values_s9_adaptive = []
    values_s9_optimized = []

    rows = []
    mode_counts = defaultdict(int)

    prev_s8_raw = None

    for i, d in enumerate(dates):
        raw_s8 = s8_asset_raw[d]

        if prev_s8_raw is None or prev_s8_raw <= 0:
            r_s8 = 0.0
        else:
            r_s8 = raw_s8 / prev_s8_raw - 1.0

        r_0050 = ret_0050.get(d, 0.0)

        # 不偷看：今天的 adaptive 權重用昨天的 regime。
        if i == 0:
            decision_date = d
            mode = "NEUTRAL"
        else:
            decision_date = dates[i - 1]
            mode = regime_map.get(decision_date, {}).get("mode", "NEUTRAL")

        w_core, w_sat, w_cash = regime_weights(mode)
        opt_core, opt_sat, opt_cash = optimized_regime_weights(mode)
        mode_counts[mode] += 1

        value_0050 *= (1.0 + r_0050)
        value_s8 *= (1.0 + r_s8)

        # 固定 50/50：一半 0050，一半 S8
        value_s9_fixed *= (1.0 + 0.50 * r_0050 + 0.50 * r_s8)

        # Adaptive：0050 / S8 / 現金比例依 regime 切換
        value_s9_adaptive *= (1.0 + w_core * r_0050 + w_sat * r_s8)

        # Optimized：使用 optimizer 找到的最佳風控權重
        value_s9_optimized *= (1.0 + opt_core * r_0050 + opt_sat * r_s8)

        values_0050.append(value_0050)
        values_s8.append(value_s8)
        values_s9_fixed.append(value_s9_fixed)
        values_s9_adaptive.append(value_s9_adaptive)
        values_s9_optimized.append(value_s9_optimized)

        rows.append({
            "date": d,
            "decision_date": decision_date,
            "regime_mode": mode,
            "w_0050_core": round(w_core, 4),
            "w_s8_satellite": round(w_sat, 4),
            "w_cash": round(w_cash, 4),
            "ret_0050_pct": round(r_0050 * 100, 4),
            "ret_s8_pct": round(r_s8 * 100, 4),
            "value_0050": round(value_0050, 2),
            "value_s8": round(value_s8, 2),
            "value_s9_fixed_50_50": round(value_s9_fixed, 2),
            "value_s9_adaptive": round(value_s9_adaptive, 2),
            "value_s9_optimized": round(value_s9_optimized, 2),
        })

        prev_s8_raw = raw_s8

    m_0050 = portfolio_metrics(values_0050)
    m_s8 = portfolio_metrics(values_s8)
    m_fixed = portfolio_metrics(values_s9_fixed)
    m_adaptive = portfolio_metrics(values_s9_adaptive)
    m_optimized = portfolio_metrics(values_s9_optimized)

    csv_path = out_dir / "s9_core_satellite_equity.csv"
    abnormal_path = out_dir / "s9_0050_abnormal_rows.csv"
    report_path = out_dir / "s9_core_satellite_report.md"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date",
            "decision_date",
            "regime_mode",
            "w_0050_core",
            "w_s8_satellite",
            "w_cash",
            "ret_0050_pct",
            "ret_s8_pct",
            "value_0050",
            "value_s8",
            "value_s9_fixed_50_50",
            "value_s9_adaptive",
            "value_s9_optimized",
        ])
        writer.writeheader()
        writer.writerows(rows)

    with abnormal_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "prev_date", "prev_close", "close", "raw_return_pct"
        ])
        writer.writeheader()
        writer.writerows(abnormal_rows)

    def row(name, m):
        ret, dd, end_value = m
        alpha = ret - m_0050[0]
        return f"| {name} | {end_value:,.2f} | {ret * 100:.2f}% | {dd * 100:.2f}% | {alpha * 100:.2f}% |"

    report = f"""# S9：0050 Core + S8 Satellite 回測報告

## 回測設定

- 初始資產：{INITIAL_ASSET:,.0f}
- 期間：{dates[0]} ～ {dates[-1]}
- 0050 benchmark：使用 clean return，排除單日異常跳價 >= {ABNORMAL_RETURN_THRESHOLD * 100:.0f}%
- 0050 異常跳價筆數：{len(abnormal_rows)}
- S8 來源：`data/reports/s8_0050_alpha_equity.csv`
- Regime 來源：`data/reports/s8_regime_audit.csv`
- Adaptive 權重使用前一交易日 regime，避免偷看未來

## 策略比較

| 策略 | 期末資產 | 報酬率 | 最大回撤 | Alpha vs 0050 |
|---|---:|---:|---:|---:|
{row("100% 0050", m_0050)}
{row("100% S8", m_s8)}
{row("S9 Fixed 50/50", m_fixed)}
{row("S9 Adaptive", m_adaptive)}
{row("S9 Optimized", m_optimized)}

## Adaptive 使用次數

| Regime | 天數 |
|---|---:|
| OFFENSIVE | {mode_counts["OFFENSIVE"]} |
| NEUTRAL | {mode_counts["NEUTRAL"]} |
| DEFENSIVE | {mode_counts["DEFENSIVE"]} |

## 判斷

"""

    if m_optimized[0] > m_0050[0]:
        report += "- S9 Optimized 有跑贏 0050，值得列為正式候選策略。\n"
    elif m_optimized[1] > m_0050[1]:
        report += "- S9 Optimized 尚未跑贏 0050，但最大回撤明顯較低，屬於風險調整後較佳的配置。\n"
    else:
        report += "- S9 Optimized 尚未跑贏 0050，且風險優勢不明顯，需要繼續改善 S8。\n"

    report += f"""

## 輸出檔案

- `{csv_path}`
- `{abnormal_path}`
- `{report_path}`
"""

    report_path.write_text(report, encoding="utf-8")

    print("S9 core-satellite report finished.")
    print(f"Report: {report_path}")
    print(f"Equity: {csv_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()
