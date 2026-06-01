"""ml_eval/data.py
從 quant.db 組出 (特徵 + 前瞻報酬標籤) 的訓練表。

v1.1 新增：籌碼趨勢特徵（rolling sum + 連續買超/賣超天數）
"""
from __future__ import annotations

import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import List

import numpy as np
import pandas as pd

from . import config


def _table_columns(con: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.OperationalError:
        return []


def _safe_cols(available: List[str], wanted: List[str]) -> List[str]:
    keep = [c for c in wanted if c in available]
    missing = [c for c in wanted if c not in available]
    if missing:
        warnings.warn(f"以下特徵欄位在資料庫中不存在，已略過：{missing}")
    return keep


def build_forward_returns(con: sqlite3.Connection, horizon: int) -> pd.DataFrame:
    """
    全市場前瞻報酬。traded=True 代表當天真的有成交（非僵屍列）。
    fwd_ret 用「市場交易日曆位移 H 天」算，carried forward price = 正確 mark-to-market。
    """
    ohlcv = pd.read_sql_query(
        "SELECT code, trade_date AS date, close, value FROM ohlcv_daily "
        "WHERE close IS NOT NULL AND close > 0", con)
    ohlcv["date"] = ohlcv["date"].astype(str)
    ohlcv = ohlcv.sort_values(["code", "date"]).reset_index(drop=True)

    g = ohlcv.groupby("code", group_keys=False)
    ohlcv["fwd_close"] = g["close"].shift(-horizon)
    ohlcv["fwd_ret"]   = ohlcv["fwd_close"] / ohlcv["close"] - 1.0

    prev_c = g["close"].shift(1)
    prev_v = g["value"].shift(1)
    ohlcv["traded"] = ~((ohlcv["close"] == prev_c) & (ohlcv["value"] == prev_v))

    return ohlcv[["code", "date", "close", "value", "fwd_ret", "traded"]]


def _streak(s: pd.Series) -> pd.Series:
    """
    連續買超/賣超天數（正值 = 連續買超，負值 = 連續賣超）。
    例：[100, 200, -50, 150, 300] → [1, 2, -1, 1, 2]
    """
    sign = np.sign(s)
    change = (sign != sign.shift(1)).cumsum()
    cum = s.groupby(change).cumcount() + 1
    return sign * cum


def build_chip_trends(con: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """
    從 chip_daily 計算籌碼趨勢特徵（rolling + streak）。
    多抓 45 天 buffer 確保 rolling window 有足夠歷史。

    新增特徵：
      foreign_3d / foreign_5d  ：外資 3/5 日累計買超
      trust_3d / trust_5d      ：投信 3/5 日累計買超
      foreign_streak / trust_streak：連續買超(正)/賣超(負)天數
    """
    buf_start = (datetime.fromisoformat(start) - timedelta(days=45)).strftime('%Y-%m-%d')

    chip = pd.read_sql_query(
        "SELECT code, trade_date AS date, foreign_net, trust_net, dealer_net "
        "FROM chip_daily WHERE trade_date >= ? ORDER BY code, date",
        con, params=[buf_start])
    chip["date"] = chip["date"].astype(str)
    chip = chip.sort_values(["code", "date"])

    g = chip.groupby("code", group_keys=False)

    chip["foreign_3d"]     = g["foreign_net"].transform(lambda x: x.rolling(3, min_periods=1).sum())
    chip["foreign_5d"]     = g["foreign_net"].transform(lambda x: x.rolling(5, min_periods=1).sum())
    chip["trust_3d"]       = g["trust_net"].transform(lambda x: x.rolling(3, min_periods=1).sum())
    chip["trust_5d"]       = g["trust_net"].transform(lambda x: x.rolling(5, min_periods=1).sum())
    chip["foreign_streak"] = g["foreign_net"].transform(_streak)
    chip["trust_streak"]   = g["trust_net"].transform(_streak)

    # 只回傳目標區間（buffer 只用來算 rolling，不納入訓練）
    chip = chip[chip["date"] >= start]
    return chip[["code", "date",
                 "foreign_3d", "foreign_5d",
                 "trust_3d", "trust_5d",
                 "foreign_streak", "trust_streak"]]


def load_dataset(db_path: str, start: str, end: str, horizon: int,
                 min_close: float, min_value: float,
                 keep_unlabeled_tail: bool = False) -> pd.DataFrame:
    """
    組出最終訓練/預測表。

    keep_unlabeled_tail=True 時，保留最新幾天 fwd_ret 尚未知的列，
    讓 latest/full mode 可以真的對「最新交易日」產生分數；訓練時仍只使用
    fwd_ret.notna() 的歷史資料，因此不會偷看未來。
    """
    con = sqlite3.connect(db_path)
    try:
        ds_cols = _table_columns(con, "daily_scores")
        if not ds_cols:
            raise RuntimeError("找不到 daily_scores 表，請確認 --db 路徑指向正確的 quant.db")

        feat_scores = _safe_cols(ds_cols, config.FEATURES_FROM_SCORES)

        sel = ", ".join(["code", "score_date"] + feat_scores)
        scores = pd.read_sql_query(
            f"SELECT {sel} FROM daily_scores WHERE score_date >= ? AND score_date <= ?",
            con, params=[start, end])
        scores = scores.rename(columns={"score_date": "date"})
        scores["date"] = scores["date"].astype(str)

        tech_cols = _table_columns(con, "technical_daily_features")
        feat_tech = _safe_cols(tech_cols, config.FEATURES_FROM_TECH)
        if feat_tech:
            sel = ", ".join(["code", "trade_date"] + feat_tech)
            tech = pd.read_sql_query(
                f"SELECT {sel} FROM technical_daily_features WHERE trade_date >= ? AND trade_date <= ?",
                con, params=[start, end]).rename(columns={"trade_date": "date"})
            tech["date"] = tech["date"].astype(str)
        else:
            tech = None

        chip_cols = _table_columns(con, "chip_daily")
        feat_chip = _safe_cols(chip_cols, config.FEATURES_FROM_CHIP)
        if feat_chip:
            sel = ", ".join(["code", "trade_date"] + feat_chip)
            chip = pd.read_sql_query(
                f"SELECT {sel} FROM chip_daily WHERE trade_date >= ? AND trade_date <= ?",
                con, params=[start, end]).rename(columns={"trade_date": "date"})
            chip["date"] = chip["date"].astype(str)
        else:
            chip = None

        # 新增：籌碼趨勢特徵
        chip_trends = None
        if _table_columns(con, "chip_daily"):
            try:
                chip_trends = build_chip_trends(con, start, end)
            except Exception as e:
                warnings.warn(f"籌碼趨勢特徵計算失敗，略過：{e}")

        fwd = build_forward_returns(con, horizon)
    finally:
        con.close()

    # 合併
    df = scores.merge(fwd, on=["code", "date"], how="inner")
    if tech is not None:
        df = df.merge(tech, on=["code", "date"], how="left")
    if chip is not None:
        df = df.merge(chip, on=["code", "date"], how="left")
    if chip_trends is not None:
        df = df.merge(chip_trends, on=["code", "date"], how="left")

    # === 資料品質清洗 ===
    quality = {"raw_merged": len(df)}

    df = df[df["code"].astype(str).str.match(config.UNIVERSE_REGEX)]
    quality["after_universe"] = len(df)

    df = df[df["traded"]]
    quality["after_traded"] = len(df)

    df = df[(df["close"] >= min_close) & (df["value"].fillna(0) >= min_value)]
    quality["after_liquidity"] = len(df)

    has_label = df["fwd_ret"].notna()
    valid_label = has_label & (df["fwd_ret"].abs() <= config.RETURN_CAP)
    if keep_unlabeled_tail:
        df = df[valid_label | (~has_label)].copy()
    else:
        df = df[valid_label].copy()
    quality["after_return_cap"] = int(valid_label.sum())
    quality["unlabeled_kept"] = int((~has_label).sum()) if keep_unlabeled_tail else 0

    if df.empty:
        df.attrs["feature_cols"] = []
        df.attrs["quality"] = quality
        return df

    labeled = df["fwd_ret"].notna()
    lo = df.loc[labeled, "fwd_ret"].quantile(config.WINSORIZE_PCT / 100)
    hi = df.loc[labeled, "fwd_ret"].quantile(1 - config.WINSORIZE_PCT / 100)
    df.loc[labeled, "fwd_ret"] = df.loc[labeled, "fwd_ret"].clip(lo, hi)

    quality["final"] = len(df)
    quality["pct_kept_of_universe"] = round(100 * quality["after_return_cap"] / max(quality["after_universe"], 1), 1)
    quality["pct_stale_dropped"] = round(
        100 * (quality["after_universe"] - quality["after_traded"]) / max(quality["after_universe"], 1), 1)

    # 特徵欄位清單
    trend_cols = ["foreign_3d", "foreign_5d", "trust_3d", "trust_5d", "foreign_streak", "trust_streak"]
    existing_trend_cols = [c for c in trend_cols if c in df.columns]
    feature_cols = feat_scores + (feat_tech or []) + (feat_chip or []) + existing_trend_cols

    for c in feature_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df[feature_cols] = df[feature_cols].fillna(df[feature_cols].median()).fillna(0)

    df = df.sort_values(["date", "code"]).reset_index(drop=True)
    df.attrs["feature_cols"] = feature_cols
    df.attrs["quality"] = quality
    return df
