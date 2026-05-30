#!/usr/bin/env python3
"""ml_scorer.py — 正式版 ML 選股評分（取代 v8_ml_scoring.py）

設計原則：完全沿用評估台已驗證的乾淨邏輯
  - 全市場（剔除僵屍列 + 流動性過濾），不被規則層污染
  - LightGBM（沒裝自動 fallback sklearn）
  - 「日期層級 + embargo」walk-forward → 每一天的歷史分數都是無洩漏的樣本外預測
  - 最新幾天（還沒有未來報酬可當標籤的）用「全部已知標籤資料」訓練的最終模型預測，
    因為這些日期在訓練窗之後，所以同樣無洩漏

輸出寫進 ml_score_results 表（你的 /api/v8/ml-scores 已經在讀它，前端零修改自動接上）：
  ml_score            = 當天橫斷面百分位 0~100（越高=模型越看好），取代舊版 pred*10+50 的粗暴縮放
  ml_rank             = 當天排名（1=最佳）
  predicted_return_5d = 模型預測的未來報酬（%），供透明參考
  model_version       = 標記版本

用法（在 twse_ml_eval 資料夾裡執行）：
  # 完整：跑整段歷史的 walk-forward + 最新日期（第一次建議用這個）
  python3 ml_scorer.py --db ../data/db/quant.db --mode full

  # 每日：只訓練+評最新 N 天，給排程器快速跑（每天收盤後）
  python3 ml_scorer.py --db ../data/db/quant.db --mode latest --score-days 1
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

from ml_eval import config, data as data_mod, models

MODEL_VERSION = "lgbm_v9_clean"


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    """把預測值轉成每日橫斷面的 ml_score(百分位0-100) 與 ml_rank（用 transform，不動欄位）。"""
    df = df.copy()
    grp = df.groupby("date")["pred"]
    df["ml_score"] = (grp.rank(pct=True) * 100).round(2)          # 當天最看好≈100
    df["ml_rank"] = grp.rank(ascending=False, method="first").astype(int)  # 1=最佳
    df["predicted_return_5d"] = (df["pred"] * 100).round(3)       # 轉成 %
    return df


def walk_forward_predict(df: pd.DataFrame, feature_cols: list, dates: list,
                         horizon: int, embargo: int, step: int,
                         min_train: int, model_name: str):
    """
    沿時間軸逐步前進：每一步用「之前的已知標籤資料」訓練，預測接下來 step 天。
    最後一步自然涵蓋「還沒有標籤的最新幾天」（= 今天的選股），且因在訓練窗之後 → 無洩漏。
    回傳 (預測結果 DataFrame, 最終模型, 最終模型特徵重要性)
    """
    preds = []
    last_model = None
    last_importance = None

    w = min_train
    while w < len(dates):
        pred_dates = dates[w: w + step]
        train_cut = w - embargo
        if train_cut < min_train:
            w += step
            continue
        train_dates = dates[:train_cut]

        tr = df[df["date"].isin(train_dates) & df["fwd_ret"].notna()]
        pr = df[df["date"].isin(pred_dates)]
        if len(tr) < 500 or pr.empty:
            w += step
            continue

        model, backend = models.make_model(model_name)
        model.fit(tr[feature_cols].values, tr["fwd_ret"].values)
        out = pr[["date", "code"]].copy()
        out["pred"] = model.predict(pr[feature_cols].values)
        preds.append(out)

        last_model = model
        if hasattr(model, "feature_importances_"):
            last_importance = dict(zip(feature_cols,
                                       [float(x) for x in model.feature_importances_]))
        w += step

    if not preds:
        return pd.DataFrame(), None, None
    allpred = pd.concat(preds, ignore_index=True)
    return add_scores(allpred), last_model, last_importance


def latest_predict(df: pd.DataFrame, feature_cols: list, dates: list,
                   score_days: int, model_name: str):
    """每日快速模式：用全部已知標籤資料訓練，只預測最新 score_days 天。"""
    pred_dates = dates[-score_days:]
    tr = df[df["fwd_ret"].notna()]                      # 所有有標籤的資料
    pr = df[df["date"].isin(pred_dates)]
    if len(tr) < 500 or pr.empty:
        return pd.DataFrame(), None, None

    model, backend = models.make_model(model_name)
    model.fit(tr[feature_cols].values, tr["fwd_ret"].values)
    out = pr[["date", "code"]].copy()
    out["pred"] = model.predict(pr[feature_cols].values)
    importance = (dict(zip(feature_cols, [float(x) for x in model.feature_importances_]))
                  if hasattr(model, "feature_importances_") else None)
    return add_scores(out), model, importance


def load_names(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT code, name FROM stock_meta").fetchall()
        return {c: n for c, n in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def write_scores(db_path: str, scored: pd.DataFrame, names: dict,
                 importance: dict | None):
    """upsert 到 ml_score_results。"""
    con = sqlite3.connect(db_path)
    imp_json = json.dumps(importance, ensure_ascii=False) if importance else None
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS ml_score_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                score_date TEXT NOT NULL, code TEXT NOT NULL, stock_name TEXT,
                ml_score REAL, ml_rank INTEGER, feature_importance TEXT,
                model_version TEXT, predicted_return_5d REAL, confidence REAL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(score_date, code))
        """)
        rows = [(r.date, r.code, names.get(r.code), r.ml_score, int(r.ml_rank),
                 imp_json, MODEL_VERSION, r.predicted_return_5d)
                for r in scored.itertuples()]
        con.executemany("""
            INSERT INTO ml_score_results
                (score_date, code, stock_name, ml_score, ml_rank,
                 feature_importance, model_version, predicted_return_5d)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(score_date, code) DO UPDATE SET
                stock_name=excluded.stock_name, ml_score=excluded.ml_score,
                ml_rank=excluded.ml_rank, feature_importance=excluded.feature_importance,
                model_version=excluded.model_version,
                predicted_return_5d=excluded.predicted_return_5d,
                created_at=datetime('now','localtime')
        """, rows)
        con.commit()
        return len(rows)
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="正式版 ML 選股評分")
    ap.add_argument("--db", default="../data/db/quant.db")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--mode", choices=["full", "latest"], default="full")
    ap.add_argument("--score-days", type=int, default=1, help="latest 模式：評最新幾天")
    ap.add_argument("--step", type=int, default=20, help="full 模式：每幾天重訓一次")
    ap.add_argument("--horizon", type=int, default=config.DEFAULT_HORIZON)
    ap.add_argument("--embargo", type=int, default=None)
    ap.add_argument("--min-train", type=int, default=40)
    ap.add_argument("--model", default="auto")
    args = ap.parse_args()
    embargo = args.embargo if args.embargo is not None else args.horizon

    print("讀取資料中（乾淨：剔除僵屍列 + 流動性過濾）...")
    df = data_mod.load_dataset(args.db, args.start, args.end, args.horizon,
                               config.DEFAULT_MIN_CLOSE, config.DEFAULT_MIN_VALUE)
    if df.empty:
        print("⚠️ 沒有資料，檢查 --db 路徑。")
        return
    feature_cols = df.attrs["feature_cols"]
    dates = sorted(df["date"].unique().tolist())
    q = df.attrs.get("quality", {})
    print(f"  {len(df):,} 列 / {len(dates)} 天 / {df['code'].nunique()} 檔 / "
          f"{len(feature_cols)} 特徵（僵屍列 {q.get('pct_stale_dropped','?')}%）")

    if args.mode == "full":
        print(f"  模式：full（walk-forward，每 {args.step} 天重訓，embargo={embargo}）")
        scored, model, importance = walk_forward_predict(
            df, feature_cols, dates, args.horizon, embargo, args.step,
            args.min_train, args.model)
    else:
        print(f"  模式：latest（評最新 {args.score_days} 天）")
        scored, model, importance = latest_predict(
            df, feature_cols, dates, args.score_days, args.model)

    if scored.empty:
        print("⚠️ 沒有產生任何分數（資料量可能太少）。")
        return

    names = load_names(args.db)
    n = write_scores(args.db, scored, names, importance)
    sd = sorted(scored["date"].unique())
    print(f"\n✓ 已寫入 ml_score_results：{n:,} 筆，涵蓋 {len(sd)} 天（{sd[0]} ~ {sd[-1]}）")

    # 印出最新一天的 Top 10 給你看
    latest = scored[scored["date"] == sd[-1]].sort_values("ml_rank").head(10)
    print(f"\n  最新交易日 {sd[-1]} ML 選股 Top 10：")
    for r in latest.itertuples():
        nm = names.get(r.code, "")
        print(f"    #{int(r.ml_rank):<3} {r.code} {nm:<10} ML分={r.ml_score:>5.1f}  預測5日={r.predicted_return_5d:+.2f}%")
    print(f"\n  前端開 /api/v8/ml-scores 或對應頁面即可看到這份排名。")


if __name__ == "__main__":
    main()
