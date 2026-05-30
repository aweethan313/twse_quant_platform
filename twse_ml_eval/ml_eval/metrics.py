"""ml_eval/metrics.py
交易意義上的評估指標。重點：MAE 沒用，這裡看的是「排序預測力」與「分層報酬」。

1. Rank IC   ：每天 Spearman(預測, 實際前瞻報酬)，跨日平均。是否 > 0 且穩定。
2. 分層回測  ：每天按預測切 N 層，看 top 層 vs bottom 層的未來報酬，含交易成本。
3. 對照基準  ：同樣指標套在 final_score 上，回答「ML 有沒有贏過規則分數」。
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import config


def rank_ic(df: pd.DataFrame, pred_col: str, target_col: str = "fwd_ret") -> Dict:
    """每個交易日算一次 Spearman 排序相關，再彙總。"""
    daily = []
    for d, g in df.groupby("date"):
        if g[pred_col].nunique() < 5 or len(g) < 5:
            continue
        ic, _ = spearmanr(g[pred_col], g[target_col])
        if not np.isnan(ic):
            daily.append(ic)
    daily = np.array(daily)
    if len(daily) == 0:
        return {"ic_mean": np.nan, "ic_std": np.nan, "ic_ir": np.nan,
                "ic_t": np.nan, "pct_positive": np.nan, "n_days": 0}
    mean, std = daily.mean(), daily.std(ddof=1) if len(daily) > 1 else np.nan
    return {
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": mean / std if std and not np.isnan(std) else np.nan,  # 資訊比率
        "ic_t": mean / std * np.sqrt(len(daily)) if std else np.nan,    # t 統計量
        "pct_positive": float((daily > 0).mean()),
        "n_days": len(daily),
    }


def decile_backtest(df: pd.DataFrame, pred_col: str, target_col: str = "fwd_ret",
                    n_deciles: int = config.N_DECILES, horizon: int = 5) -> Dict:
    """
    每天按預測值切層，計算各層的平均前瞻報酬（已是 H 日報酬）。
    回傳毛報酬與扣成本後淨報酬，並換算成大略的年化（252/H 個持有期）。
    """
    top_rets, bot_rets, ls_rets, top_hit = [], [], [], []
    decile_profile = {q: [] for q in range(n_deciles)}

    for d, g in df.groupby("date"):
        if len(g) < n_deciles * 2:
            continue
        try:
            g = g.copy()
            g["q"] = pd.qcut(g[pred_col].rank(method="first"), n_deciles, labels=False)
        except ValueError:
            continue
        means = g.groupby("q")[target_col].mean()
        for q in range(n_deciles):
            if q in means.index:
                decile_profile[q].append(means[q])
        top = means.get(n_deciles - 1, np.nan)
        bot = means.get(0, np.nan)
        top_rets.append(top)
        bot_rets.append(bot)
        ls_rets.append(top - bot)
        # top 層命中率（正報酬比例）
        topg = g[g["q"] == n_deciles - 1]
        top_hit.append((topg[target_col] > 0).mean())

    def _agg(x):
        x = np.array([v for v in x if not np.isnan(v)])
        return x.mean() if len(x) else np.nan

    top_g = _agg(top_rets)        # top 層每持有期毛報酬
    bot_g = _agg(bot_rets)
    ls_g = _agg(ls_rets)
    rt = config.round_trip_cost()

    # 每個持有期 = H 交易日，一年約 252/H 個持有期
    periods_per_year = 252.0 / horizon
    top_net = top_g - rt                  # 純多單：一次來回成本
    ls_net = ls_g - 2 * rt                # 多空：兩腿都有成本（且做空在台股有借券成本，這裡僅近似）

    return {
        "n_days": len([v for v in top_rets if not np.isnan(v)]),
        "top_gross_per_period": top_g,
        "bottom_gross_per_period": bot_g,
        "long_short_gross_per_period": ls_g,
        "top_net_per_period": top_net,
        "long_short_net_per_period": ls_net,
        "top_hit_rate": _agg(top_hit),
        "top_net_annualized": (1 + top_net) ** periods_per_year - 1
        if not np.isnan(top_net) else np.nan,
        "decile_profile": {q: _agg(v) for q, v in decile_profile.items()},
        "round_trip_cost": rt,
    }
