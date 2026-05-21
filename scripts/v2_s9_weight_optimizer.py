from __future__ import annotations

from pathlib import Path
import csv
import math
import pandas as pd


INITIAL_ASSET = 200_000.0


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


def max_drawdown(values):
    peak = values[0]
    max_dd = 0.0

    for v in values:
        peak = max(peak, v)
        dd = v / peak - 1.0 if peak > 0 else 0.0
        max_dd = min(max_dd, dd)

    return max_dd


def metrics(values):
    ret = values[-1] / INITIAL_ASSET - 1.0
    dd = max_drawdown(values)
    score = ret / abs(dd) if dd < 0 else 0.0
    return ret, dd, values[-1], score


def run_weight_test(df, weights):
    value = INITIAL_ASSET
    values = []

    for _, r in df.iterrows():
        mode = str(r["regime_mode"])
        r0050 = safe_float(r["ret_0050_pct"]) / 100.0
        rs8 = safe_float(r["ret_s8_pct"]) / 100.0

        w0050, ws8, wcash = weights.get(mode, weights["NEUTRAL"])

        value *= 1.0 + w0050 * r0050 + ws8 * rs8
        values.append(value)

    return metrics(values)


def main():
    in_path = Path("data/reports/s9_core_satellite_equity.csv")
    out_dir = Path("data/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise FileNotFoundError("請先跑 scripts.v2_s9_core_satellite_report")

    df = pd.read_csv(in_path)

    results = []

    # 固定比較基準
    fixed_cases = {
        "fixed_100_0050": {
            "OFFENSIVE": (1.00, 0.00, 0.00),
            "NEUTRAL": (1.00, 0.00, 0.00),
            "DEFENSIVE": (1.00, 0.00, 0.00),
        },
        "fixed_50_50": {
            "OFFENSIVE": (0.50, 0.50, 0.00),
            "NEUTRAL": (0.50, 0.50, 0.00),
            "DEFENSIVE": (0.50, 0.50, 0.00),
        },
        "fixed_70_30": {
            "OFFENSIVE": (0.70, 0.30, 0.00),
            "NEUTRAL": (0.70, 0.30, 0.00),
            "DEFENSIVE": (0.70, 0.30, 0.00),
        },
    }

    for name, weights in fixed_cases.items():
        ret, dd, end_value, score = run_weight_test(df, weights)
        results.append({
            "name": name,
            "offensive": weights["OFFENSIVE"],
            "neutral": weights["NEUTRAL"],
            "defensive": weights["DEFENSIVE"],
            "end_value": round(end_value, 2),
            "return_pct": round(ret * 100, 2),
            "max_drawdown_pct": round(dd * 100, 2),
            "return_drawdown_score": round(score, 4),
        })

    # Grid search：不要太誇張，先測合理範圍
    offensive_list = [
        (0.60, 0.35, 0.05),
        (0.70, 0.25, 0.05),
        (0.80, 0.15, 0.05),
        (0.90, 0.10, 0.00),
        (1.00, 0.00, 0.00),
    ]

    neutral_list = [
        (0.50, 0.35, 0.15),
        (0.60, 0.25, 0.15),
        (0.70, 0.20, 0.10),
        (0.80, 0.10, 0.10),
    ]

    defensive_list = [
        (0.40, 0.10, 0.50),
        (0.50, 0.10, 0.40),
        (0.60, 0.10, 0.30),
        (0.70, 0.05, 0.25),
        (0.80, 0.00, 0.20),
    ]

    for ow in offensive_list:
        for nw in neutral_list:
            for dw in defensive_list:
                weights = {
                    "OFFENSIVE": ow,
                    "NEUTRAL": nw,
                    "DEFENSIVE": dw,
                }

                ret, dd, end_value, score = run_weight_test(df, weights)

                results.append({
                    "name": "grid",
                    "offensive": ow,
                    "neutral": nw,
                    "defensive": dw,
                    "end_value": round(end_value, 2),
                    "return_pct": round(ret * 100, 2),
                    "max_drawdown_pct": round(dd * 100, 2),
                    "return_drawdown_score": round(score, 4),
                })

    results.sort(
        key=lambda x: (
            x["return_pct"],
            x["return_drawdown_score"],
            -abs(x["max_drawdown_pct"]),
        ),
        reverse=True,
    )

    out_path = out_dir / "s9_weight_optimizer.csv"
    md_path = out_dir / "s9_weight_optimizer_report.md"

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "name",
            "offensive",
            "neutral",
            "defensive",
            "end_value",
            "return_pct",
            "max_drawdown_pct",
            "return_drawdown_score",
        ])
        writer.writeheader()
        writer.writerows(results)

    top = results[:20]

    lines = []
    lines.append("# S9 權重最佳化報告")
    lines.append("")
    lines.append("## Top 20 by return")
    lines.append("")
    lines.append("| rank | name | return | max DD | score | offensive | neutral | defensive |")
    lines.append("|---:|---|---:|---:|---:|---|---|---|")

    for i, r in enumerate(top, start=1):
        lines.append(
            f"| {i} | {r['name']} | {r['return_pct']:.2f}% | "
            f"{r['max_drawdown_pct']:.2f}% | {r['return_drawdown_score']:.4f} | "
            f"{r['offensive']} | {r['neutral']} | {r['defensive']} |"
        )

    # 風險控制版：最大回撤 <= 12%
    risk_ok = [r for r in results if r["max_drawdown_pct"] >= -12.0]
    risk_ok.sort(
        key=lambda x: (x["return_pct"], x["return_drawdown_score"]),
        reverse=True,
    )

    lines.append("")
    lines.append("## Top 20 with max drawdown >= -12%")
    lines.append("")
    lines.append("| rank | name | return | max DD | score | offensive | neutral | defensive |")
    lines.append("|---:|---|---:|---:|---:|---|---|---|")

    for i, r in enumerate(risk_ok[:20], start=1):
        lines.append(
            f"| {i} | {r['name']} | {r['return_pct']:.2f}% | "
            f"{r['max_drawdown_pct']:.2f}% | {r['return_drawdown_score']:.4f} | "
            f"{r['offensive']} | {r['neutral']} | {r['defensive']} |"
        )

    md_path.write_text("\n".join(lines), encoding="utf-8")

    print("S9 weight optimizer finished.")
    print(f"CSV: {out_path}")
    print(f"Report: {md_path}")
    print()
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main()
