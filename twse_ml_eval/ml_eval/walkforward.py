"""ml_eval/walkforward.py
日期層級的 purged walk-forward 切割器。

這是修正原本 v8_ml_scoring.py 最嚴重 bug 的核心：
  - 原本用 TimeSeriesSplit 按「列」切，一天有 ~200 檔，gap 完全沒隔到日子。
  - 這裡按「交易日」切，並在 train 末端挖掉 embargo（= 標籤天數）個交易日，
    確保 train 的標籤（看 H 天後）不會偷看到 val 期間的價格。

範例（embargo=5）：
  train 最後一天 D 的標籤用到 D+5 的收盤；
  只要 val 從 D+6 之後才開始，就不會重疊 → 無洩漏。
"""
from __future__ import annotations

from typing import Iterator, List, Tuple


def purged_walkforward_splits(
    dates: List[str],
    n_splits: int,
    embargo: int,
    min_train_dates: int,
) -> Iterator[Tuple[List[str], List[str]]]:
    """
    參數
    ----
    dates : 已排序、去重的交易日字串清單
    n_splits : 折數
    embargo : train 末端要挖掉幾個交易日（通常 = 標籤 horizon）
    min_train_dates : train 至少要有幾個交易日，否則跳過該折

    產出
    ----
    (train_dates, val_dates) 一對一對地 yield
    """
    dates = sorted(set(dates))
    n = len(dates)
    if n < (n_splits + 1) * 2:
        raise ValueError(
            f"交易日數量太少（{n}），不足以切 {n_splits} 折。"
            f"請縮小 n_splits 或拉長日期區間。"
        )

    # 把時間軸切成 n_splits+1 段；第 0 段全給最早一折當 train 起點，
    # 之後每段輪流當 val。採用 expanding window（train 一直長大）。
    fold_size = n // (n_splits + 1)

    for i in range(n_splits):
        val_start = (i + 1) * fold_size
        # 最後一折把剩下的全吃掉
        val_end = n if i == n_splits - 1 else (i + 2) * fold_size

        train_end = val_start - embargo  # 關鍵：挖掉 embargo 個交易日
        if train_end < min_train_dates:
            continue

        train_dates = dates[:train_end]
        val_dates = dates[val_start:val_end]
        if not val_dates:
            continue
        yield train_dates, val_dates
