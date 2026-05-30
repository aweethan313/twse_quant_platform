"""ml_eval/report.py
把結果印成人看得懂的報告，並輸出 json / csv / markdown。
重點是最後那段「結論」：直接回答「ML 有沒有贏過 final_score、訊號有沒有用」。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd


def _fmt(x, pct=False, dp=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "  n/a"
    return f"{x*100:.2f}%" if pct else f"{x:.{dp}f}"


def verdict(ml_ic: Dict, base_ic: Dict, ml_bt: Dict) -> List[str]:
    """根據指標給出白話結論。刻意保守。"""
    lines = []
    ic = ml_ic["ic_mean"]
    ic_t = ml_ic["ic_t"]
    base = base_ic["ic_mean"]
    top_net = ml_bt["top_net_per_period"]

    # 1. 訊號本身有沒有預測力
    if np.isnan(ic):
        lines.append("• 資料不足，無法判斷預測力。")
    elif ic <= 0:
        lines.append(f"• ML 的 Rank IC = {ic:.4f} ≤ 0：基本上沒有正向預測力，等同丟銅板甚至更差。")
    elif ic < 0.02:
        lines.append(f"• ML 的 Rank IC = {ic:.4f}：偏弱（量化界一般認為 0.02~0.03 才勉強可用）。")
    else:
        sig = "（且 t 值夠大，較可信）" if (not np.isnan(ic_t) and abs(ic_t) > 2) else "（但 t 值偏小，可能是運氣）"
        lines.append(f"• ML 的 Rank IC = {ic:.4f}：有一定預測力 {sig}。")

    # 2. 有沒有贏過規則分數
    if not np.isnan(ic) and not np.isnan(base):
        if ic > base + 0.005:
            lines.append(f"• ML（{ic:.4f}）贏過 final_score 基準（{base:.4f}）：ML 這層有加分，值得繼續。")
        elif ic < base - 0.005:
            lines.append(f"• ML（{ic:.4f}）輸給 final_score 基準（{base:.4f}）：目前 ML 是負貢獻，不如直接用規則分數排序。")
        else:
            lines.append(f"• ML（{ic:.4f}）與 final_score 基準（{base:.4f}）大致打平：ML 還沒帶來額外價值。")

    # 3. 扣成本後還活不活
    if not np.isnan(top_net):
        if top_net > 0:
            lines.append(f"• Top 層扣台股交易成本後，每持有期淨報酬 {top_net*100:.2f}% > 0：成本這關有過。")
        else:
            lines.append(f"• Top 層扣成本後每持有期淨報酬 {top_net*100:.2f}% ≤ 0：毛報酬被 0.585% 來回成本吃光，這是最常見的死法。")

    return lines


def print_report(meta: Dict, fold_df: pd.DataFrame,
                 ml_ic: Dict, base_ic: Dict,
                 ml_bt: Dict, base_bt: Dict, importance: pd.Series):
    line = "=" * 64
    print(f"\n{line}\n  TWSE 日線 ML 誠實評估報告\n{line}")
    print(f"資料庫        : {meta['db']}")
    print(f"區間          : {meta['start']} ~ {meta['end']}")
    print(f"模型後端      : {meta['backend']}")
    print(f"標籤          : 未來 {meta['horizon']} 交易日報酬")
    print(f"embargo       : {meta['embargo']} 交易日（避免標籤洩漏）")
    print(f"樣本數        : {meta['n_rows']:,} 列 / {meta['n_dates']} 個交易日 / {meta['n_codes']} 檔")
    print(f"特徵數        : {meta['n_features']}")
    print(f"來回交易成本  : {ml_bt['round_trip_cost']*100:.3f}%")

    q = meta.get("quality")
    if q:
        print(f"\n--- 資料品質（清洗過程）---")
        print(f"  合併後原始列數          : {q.get('raw_merged',0):,}")
        print(f"  四位數代號過濾後        : {q.get('after_universe',0):,}")
        print(f"  剔除僵屍列後            : {q.get('after_traded',0):,}  (僵屍列佔比 {q.get('pct_stale_dropped','?')}%)")
        print(f"  流動性過濾後            : {q.get('after_liquidity',0):,}")
        print(f"  剔除極端報酬後          : {q.get('after_return_cap',0):,}")
        if q.get('pct_stale_dropped', 0) and q['pct_stale_dropped'] > 30:
            print(f"  ⚠️ 僵屍列高達 {q['pct_stale_dropped']}%：你的 ohlcv_daily 有嚴重的 stale/帶上來資料問題，建議先修資料源。")

    print(f"\n--- 各折 Rank IC（樣本外）---")
    print(fold_df.to_string(index=False))

    print(f"\n--- 整體 Rank IC（跨所有 val 日）---")
    print(f"{'':14}{'ML':>12}{'final_score':>14}")
    print(f"{'IC 平均':14}{_fmt(ml_ic['ic_mean']):>12}{_fmt(base_ic['ic_mean']):>14}")
    print(f"{'IC 標準差':14}{_fmt(ml_ic['ic_std']):>12}{_fmt(base_ic['ic_std']):>14}")
    print(f"{'IC 資訊比率':14}{_fmt(ml_ic['ic_ir']):>12}{_fmt(base_ic['ic_ir']):>14}")
    print(f"{'IC t 值':14}{_fmt(ml_ic['ic_t'],dp=2):>12}{_fmt(base_ic['ic_t'],dp=2):>14}")
    print(f"{'IC>0 天數佔比':14}{_fmt(ml_ic['pct_positive'],pct=True):>12}{_fmt(base_ic['pct_positive'],pct=True):>14}")

    print(f"\n--- 分層回測（每持有期 = {meta['horizon']} 交易日）---")
    print(f"{'':22}{'ML':>14}{'final_score':>14}")
    print(f"{'Top 層毛報酬':22}{_fmt(ml_bt['top_gross_per_period'],pct=True):>14}{_fmt(base_bt['top_gross_per_period'],pct=True):>14}")
    print(f"{'Bottom 層毛報酬':22}{_fmt(ml_bt['bottom_gross_per_period'],pct=True):>14}{_fmt(base_bt['bottom_gross_per_period'],pct=True):>14}")
    print(f"{'多空(top-bot)毛':22}{_fmt(ml_bt['long_short_gross_per_period'],pct=True):>14}{_fmt(base_bt['long_short_gross_per_period'],pct=True):>14}")
    print(f"{'Top 層淨報酬(扣成本)':22}{_fmt(ml_bt['top_net_per_period'],pct=True):>14}{_fmt(base_bt['top_net_per_period'],pct=True):>14}")
    print(f"{'Top 層命中率':22}{_fmt(ml_bt['top_hit_rate'],pct=True):>14}{_fmt(base_bt['top_hit_rate'],pct=True):>14}")
    print(f"{'Top 淨報酬(粗略年化)':22}{_fmt(ml_bt['top_net_annualized'],pct=True):>14}{_fmt(base_bt['top_net_annualized'],pct=True):>14}")

    print(f"\n--- 分層報酬輪廓（ML，第0層=預測最差 → 第{meta['n_deciles']-1}層=最好）---")
    prof = ml_bt["decile_profile"]
    bars = []
    for q in range(meta["n_deciles"]):
        v = prof.get(q, np.nan)
        bars.append(f"  層{q}: {_fmt(v,pct=True):>8}")
    print("\n".join(bars))
    print("  （理想：層數越高、報酬越高，呈單調遞增）")

    if importance is not None and len(importance):
        print(f"\n--- 特徵重要性 Top 12（最後一折模型）---")
        for name, imp in importance.head(12).items():
            print(f"  {name:20} {imp:.4f}")

    print(f"\n{line}\n  結論\n{line}")
    for ln in verdict(ml_ic, base_ic, ml_bt):
        print(ln)
    print(line)


def save_outputs(out_dir: str, meta: Dict, fold_df: pd.DataFrame,
                 ml_ic: Dict, base_ic: Dict, ml_bt: Dict, base_bt: Dict,
                 importance: pd.Series):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    fold_df.to_csv(out / "fold_metrics.csv", index=False, encoding="utf-8-sig")

    payload = {
        "meta": meta,
        "ml": {"rank_ic": ml_ic, "decile_backtest": ml_bt},
        "baseline_final_score": {"rank_ic": base_ic, "decile_backtest": base_bt},
        "verdict": verdict(ml_ic, base_ic, ml_bt),
    }
    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=float)

    # 簡短 markdown 摘要
    md = [f"# TWSE 日線 ML 評估摘要", "",
          f"- 區間：{meta['start']} ~ {meta['end']}",
          f"- 模型：{meta['backend']}，標籤 = 未來 {meta['horizon']} 交易日報酬，embargo = {meta['embargo']}",
          f"- 樣本：{meta['n_rows']:,} 列 / {meta['n_dates']} 交易日 / {meta['n_codes']} 檔",
          "", "## 整體 Rank IC", "",
          "| 指標 | ML | final_score |", "|---|---|---|",
          f"| IC 平均 | {_fmt(ml_ic['ic_mean'])} | {_fmt(base_ic['ic_mean'])} |",
          f"| IC t 值 | {_fmt(ml_ic['ic_t'],dp=2)} | {_fmt(base_ic['ic_t'],dp=2)} |",
          f"| IC>0 佔比 | {_fmt(ml_ic['pct_positive'],pct=True)} | {_fmt(base_ic['pct_positive'],pct=True)} |",
          "", "## 分層回測（每持有期）", "",
          "| 指標 | ML | final_score |", "|---|---|---|",
          f"| Top 層毛報酬 | {_fmt(ml_bt['top_gross_per_period'],pct=True)} | {_fmt(base_bt['top_gross_per_period'],pct=True)} |",
          f"| Top 層淨報酬 | {_fmt(ml_bt['top_net_per_period'],pct=True)} | {_fmt(base_bt['top_net_per_period'],pct=True)} |",
          f"| 多空毛報酬 | {_fmt(ml_bt['long_short_gross_per_period'],pct=True)} | {_fmt(base_bt['long_short_gross_per_period'],pct=True)} |",
          "", "## 結論", ""]
    md += [f"- {ln[2:] if ln.startswith('• ') else ln}" for ln in verdict(ml_ic, base_ic, ml_bt)]
    with open(out / "summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"\n✓ 已輸出：{out/'results.json'}")
    print(f"✓ 已輸出：{out/'fold_metrics.csv'}")
    print(f"✓ 已輸出：{out/'summary.md'}")
