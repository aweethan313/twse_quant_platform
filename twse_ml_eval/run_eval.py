#!/usr/bin/env python3
"""run_eval.py — TWSE 日線 ML 誠實評估台（CLI 入口）

它回答三個問題：
  1. 你的訊號到底有沒有「樣本外」的預測力？（Rank IC）
  2. ML 有沒有贏過直接用 final_score 排序？（對照基準）
  3. 扣掉台股交易成本後，Top 層還賺不賺？（分層回測淨報酬）

全程用「日期層級 + embargo」的 purged walk-forward，杜絕標籤洩漏。

用法（在你的專案根目錄執行）：
  python3 run_eval.py
  python3 run_eval.py --db data/db/quant.db --horizon 5 --n-splits 5
  python3 run_eval.py --start 2025-03-01 --end 2026-05-01 --model lgbm
  python3 run_eval.py --model rf --out data/ml_eval_rf
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from ml_eval import config, data as data_mod, models, metrics, report
from ml_eval.walkforward import purged_walkforward_splits


def parse_args():
    p = argparse.ArgumentParser(description="TWSE 日線 ML 誠實評估台")
    p.add_argument("--db", default=config.DEFAULT_DB, help="quant.db 路徑")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2026-12-31")
    p.add_argument("--horizon", type=int, default=config.DEFAULT_HORIZON,
                   help="前瞻報酬天數（交易日），同時也是預設 embargo")
    p.add_argument("--embargo", type=int, default=config.DEFAULT_EMBARGO,
                   help="train 末端挖掉的交易日數，預設 = horizon")
    p.add_argument("--n-splits", type=int, default=config.DEFAULT_N_SPLITS)
    p.add_argument("--min-close", type=float, default=config.DEFAULT_MIN_CLOSE)
    p.add_argument("--min-value", type=float, default=config.DEFAULT_MIN_VALUE)
    p.add_argument("--model", default="auto", choices=["auto", "lgbm", "gbr", "rf"])
    p.add_argument("--out", default=config.DEFAULT_OUT)
    return p.parse_args()


def main():
    args = parse_args()
    embargo = args.embargo if args.embargo is not None else args.horizon

    print("讀取資料中...")
    df = data_mod.load_dataset(
        args.db, args.start, args.end, args.horizon,
        args.min_close, args.min_value,
    )
    feature_cols = df.attrs["feature_cols"]
    if df.empty or not feature_cols:
        print("⚠️ 沒有可用資料。檢查 --db 路徑、--start/--end 區間，或資料庫是否已灌分數與技術指標。")
        sys.exit(1)

    dates = sorted(df["date"].unique().tolist())
    print(f"  {len(df):,} 列 / {len(dates)} 交易日 / {df['code'].nunique()} 檔 / {len(feature_cols)} 特徵")

    # ---- walk-forward 迴圈 ----
    model_proto, backend = models.make_model(args.model)
    print(f"  模型後端：{backend}")

    ml_val_frames = []     # 收集所有 val 折的 (date, code, pred, fwd_ret, final_score)
    fold_rows = []
    last_importance = None

    fold_no = 0
    for train_dates, val_dates in purged_walkforward_splits(
        dates, args.n_splits, embargo, config.DEFAULT_MIN_TRAIN_DATES
    ):
        fold_no += 1
        tr = df[df["date"].isin(train_dates)]
        va = df[df["date"].isin(val_dates)]
        if len(tr) < 200 or len(va) < 50:
            continue

        X_tr, y_tr = tr[feature_cols].values, tr["fwd_ret"].values
        X_va = va[feature_cols].values

        model, _ = models.make_model(args.model)
        model.fit(X_tr, y_tr)
        pred = model.predict(X_va)

        vframe = va[["date", "code", "fwd_ret", config.BASELINE_COL]].copy()
        vframe["pred"] = pred
        ml_val_frames.append(vframe)

        fold_ic = metrics.rank_ic(vframe.rename(columns={"pred": "p"}), "p")
        fold_rows.append({
            "fold": fold_no,
            "train_days": len(train_dates),
            "val_days": len(val_dates),
            "val_start": val_dates[0],
            "val_end": val_dates[-1],
            "val_rows": len(va),
            "ic_mean": round(fold_ic["ic_mean"], 4) if not np.isnan(fold_ic["ic_mean"]) else None,
            "ic_pos_pct": round(fold_ic["pct_positive"], 3) if not np.isnan(fold_ic["pct_positive"]) else None,
        })

        # 取得特徵重要性（若模型支援）
        if hasattr(model, "feature_importances_"):
            last_importance = pd.Series(
                model.feature_importances_, index=feature_cols
            ).sort_values(ascending=False)

    if not ml_val_frames:
        print("⚠️ 沒有任何有效的 walk-forward 折。請減少 --n-splits 或拉長日期區間。")
        sys.exit(1)

    allval = pd.concat(ml_val_frames, ignore_index=True)

    # ---- 整體指標：ML vs final_score 基準 ----
    ml_ic = metrics.rank_ic(allval.rename(columns={"pred": "p"}), "p")
    base_ic = metrics.rank_ic(allval, config.BASELINE_COL)
    ml_bt = metrics.decile_backtest(allval.rename(columns={"pred": "p"}), "p",
                                    horizon=args.horizon)
    base_bt = metrics.decile_backtest(allval, config.BASELINE_COL,
                                      horizon=args.horizon)

    meta = {
        "db": args.db, "start": args.start, "end": args.end,
        "backend": backend, "horizon": args.horizon, "embargo": embargo,
        "n_rows": len(df), "n_dates": len(dates), "n_codes": int(df["code"].nunique()),
        "n_features": len(feature_cols), "n_deciles": config.N_DECILES,
        "quality": df.attrs.get("quality"),
    }
    fold_df = pd.DataFrame(fold_rows)

    report.print_report(meta, fold_df, ml_ic, base_ic, ml_bt, base_bt, last_importance)
    report.save_outputs(args.out, meta, fold_df, ml_ic, base_ic, ml_bt, base_bt, last_importance)


if __name__ == "__main__":
    main()
