#!/usr/bin/env python3
"""experiment_valuation.py — 用資料回答「該不該把 valuation_score 拿掉」

它做三件事（全部用樣本外、剔除僵屍列的乾淨資料）：
  1. 算每個 component score 各自的 Rank IC → 看誰有預測力、誰拖後腿
  2. 把「IC 為正」的 component 等權組合（先做橫斷面標準化），算它的 IC
  3. 跟你現在的 final_score 並排比較 → 證明「丟掉壞因子的簡單組合」是否就贏過手刻分數

用法（在 twse_ml_eval 資料夾裡執行）：
  python3 experiment_valuation.py --db ../data/db/quant.db
  python3 experiment_valuation.py --db ../data/db/quant.db --horizon 5
"""
from __future__ import annotations

import argparse
import warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore", message="An input array is constant")

from ml_eval import config, data as data_mod

# 要檢驗的 component 分數（會自動略過資料庫沒有的）
COMPONENTS = [
    "fundamental_score", "valuation_score", "chip_score", "momentum_score",
    "macro_score", "news_score", "core_score", "risk_score",
    "candidate_score", "entry_score", "volume_score",
]


def daily_rank_ic(df: pd.DataFrame, col: str, target: str = "fwd_ret") -> dict:
    """每天 Spearman(分數, 未來報酬)，跨日彙總。"""
    daily = []
    for _, g in df.groupby("date"):
        s = g[col]
        if s.nunique() < 5:          # 同一天值幾乎都一樣（如 macro_score）→ 無法算橫斷面排序
            continue
        ic, _ = spearmanr(s, g[target])
        if not np.isnan(ic):
            daily.append(ic)
    if not daily:
        return dict(ic=np.nan, t=np.nan, pos=np.nan, n=0)
    a = np.array(daily)
    s = a.std(ddof=1) if len(a) > 1 else np.nan
    return dict(ic=a.mean(),
                t=a.mean() / s * np.sqrt(len(a)) if s else np.nan,
                pos=(a > 0).mean(), n=len(a))


def zscore_by_date(df: pd.DataFrame, col: str) -> pd.Series:
    """橫斷面標準化：每天把該欄位轉成 z 分數，這樣不同因子才能等權相加。"""
    g = df.groupby("date")[col]
    return (df[col] - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="../data/db/quant.db")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()

    print("讀取資料中（同評估台的清洗：剔除僵屍列 + 流動性過濾）...")
    df = data_mod.load_dataset(args.db, args.start, args.end, args.horizon,
                               config.DEFAULT_MIN_CLOSE, config.DEFAULT_MIN_VALUE)
    if df.empty:
        print("⚠️ 沒有資料，檢查 --db 路徑。")
        return

    q = df.attrs.get("quality", {})
    print(f"  樣本 {len(df):,} 列 / {df['date'].nunique()} 天 / {df['code'].nunique()} 檔"
          f"（僵屍列佔比 {q.get('pct_stale_dropped','?')}%）\n")

    comps = [c for c in COMPONENTS if c in df.columns]

    # === 1. 各 component 的 IC ===
    print("=" * 58)
    print("  各 component score 的樣本外 Rank IC")
    print("=" * 58)
    print(f"{'':2}{'component':22}{'IC':>9}{'t值':>8}{'IC>0%':>8}")
    print("-" * 58)
    rows = []
    for c in comps:
        r = daily_rank_ic(df, c)
        rows.append((c, r["ic"], r["t"], r["pos"]))
    rows.sort(key=lambda x: (np.nan_to_num(x[1], nan=-9)), reverse=True)
    positive, negative = [], []
    for c, ic, t, pos in rows:
        if np.isnan(ic):
            flag, note = "·", "（市場擇時訊號，無橫斷面排序）"
        elif ic > 0.01 and (not np.isnan(t) and t > 2):
            flag, note = "✓", "強"
            positive.append(c)
        elif ic > 0:
            flag, note = "△", "弱正"
            positive.append(c)
        else:
            flag, note = "✗", "拖後腿"
            negative.append(c)
        ic_s = f"{ic:>9.4f}" if not np.isnan(ic) else f"{'n/a':>9}"
        t_s = f"{t:>8.2f}" if not np.isnan(t) else f"{'n/a':>8}"
        pos_s = f"{pos*100:>7.1f}%" if not np.isnan(pos) else f"{'n/a':>8}"
        print(f"{flag} {c:22}{ic_s}{t_s}{pos_s}  {note}")

    # === 2. final_score 基準 ===
    base = daily_rank_ic(df, config.BASELINE_COL)

    # === 3. 只用正 IC 因子的等權組合 ===
    if positive:
        zcols = []
        for c in positive:
            zc = f"_z_{c}"
            df[zc] = zscore_by_date(df, c)
            zcols.append(zc)
        df["_positive_composite"] = df[zcols].mean(axis=1)
        combo = daily_rank_ic(df, "_positive_composite")
    else:
        combo = dict(ic=np.nan, t=np.nan, pos=np.nan, n=0)

    print("\n" + "=" * 58)
    print("  組合比較：你的 final_score  vs  丟掉壞因子的等權組合")
    print("=" * 58)
    print(f"{'':30}{'IC':>9}{'t值':>8}{'IC>0%':>8}")
    print(f"{'final_score（現況）':30}{base['ic']:>9.4f}{base['t']:>8.2f}{base['pos']*100:>7.1f}%")
    if not np.isnan(combo['ic']):
        print(f"{'正IC因子等權組合':30}{combo['ic']:>9.4f}{combo['t']:>8.2f}{combo['pos']*100:>7.1f}%")
        print(f"\n  正IC組合用到的因子：{', '.join(positive)}")
        print(f"  被排除（拖後腿）的因子：{', '.join(negative) if negative else '無'}")

    # === 結論 ===
    print("\n" + "=" * 58)
    print("  結論")
    print("=" * 58)
    if negative:
        worst = min(rows, key=lambda x: np.nan_to_num(x[1], nan=9))
        print(f"• 最該拿掉：{worst[0]}（IC={worst[1]:.4f}），它在反向預測。")
    if not np.isnan(combo['ic']) and not np.isnan(base['ic']):
        if combo['ic'] > base['ic'] + 0.005:
            print(f"• 丟掉壞因子的簡單等權組合（IC={combo['ic']:.4f}）已經贏過 final_score（{base['ic']:.4f}）。")
            print("  → 建議：把上面『拖後腿』的因子從 final_score 的計算式移除或調降權重，重算分數。")
        else:
            print(f"• 簡單重組（{combo['ic']:.4f}）跟 final_score（{base['ic']:.4f}）差不多。")
            print("  → 光調權重幫助有限，真正的提升要靠 ML（評估台已驗證 ML 贏過 final_score）。")
    print("=" * 58)


if __name__ == "__main__":
    main()
