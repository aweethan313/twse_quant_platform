"""ml_eval/config.py
集中管理預設值、特徵清單、台股交易成本。
改這裡就好，不要散落在各模組。
"""
from __future__ import annotations

# ---- 路徑 ----
DEFAULT_DB = "data/db/quant.db"          # 相對於你的專案根目錄
DEFAULT_OUT = "data/ml_eval"             # 報告輸出資料夾

# ---- 標籤 / walk-forward ----
DEFAULT_HORIZON = 5        # 前瞻報酬天數（交易日）。標籤 = 持有 H 個交易日的報酬
DEFAULT_EMBARGO = None     # None = 自動等於 horizon（這是避免標籤洩漏的關鍵）
DEFAULT_N_SPLITS = 5       # walk-forward 折數
DEFAULT_MIN_TRAIN_DATES = 40  # 一折至少要有幾個交易日的訓練資料才算數

# ---- 股票池（流動性過濾，避免雞蛋水餃股污染結果）----
DEFAULT_MIN_CLOSE = 10.0          # 最低股價
DEFAULT_MIN_VALUE = 20_000_000.0  # 當日最低成交金額（元）。約 2000 萬，濾掉冷門股
# 只留「四位數字」代號 = 普通股 + 一般 ETF（如 2330, 1101, 0050, 0056）。
# 排除特別股(2882B)、債券/受益證券(01010T)、ETN、權證、槓桿反向(00631L)等
# 幾乎不交易、報酬恆為 0 的標的——這些會嚴重污染 Rank IC 與分層回測。
UNIVERSE_REGEX = r"^[0-9]{4}$"

# ---- 標籤穩健化 ----
# 你的 ohlcv_daily 有資料品質問題（大量僵屍/帶上來的列、假日壞報價），
# 以下兩道防線把明顯的髒資料擋掉，並在報告中統計被丟掉多少，讓你知道污染程度。
RETURN_CAP = 0.60     # 丟掉 |未來報酬| > 60% 的列（5日內幾乎不可能，多半是除權息/分割/壞報價）
WINSORIZE_PCT = 0.5   # 通過上限後，再對上下各 0.5% 做 winsorize 收尾

# ---- 分層回測 ----
N_DECILES = 10        # 分幾層

# ---- 台股交易成本（單位：比例，0.001425 = 0.1425%）----
# 來回成本 = 手續費買 + 手續費賣 + 證交稅(賣)
COMMISSION = 0.001425   # 手續費，買賣各一次
SELL_TAX = 0.003        # 證券交易稅，僅賣出（當沖減半，這裡用一般現股）
def round_trip_cost() -> float:
    """一次完整進出場（買+賣）的成本比例。"""
    return COMMISSION * 2 + SELL_TAX   # ≈ 0.585%

# ---- 特徵清單 ----
# 重要原則：只放「決策當下已知」的欄位（trailing / 同日），絕不放未來資訊。
# daily_scores 的 ret1/ret5/ret20 因為無法 100% 確認是 trailing，保守起見不納入；
# 改用 technical_daily_features 裡已確認是 trailing 的 return_5d / return_20d。

FEATURES_FROM_SCORES = [
    "fundamental_score", "valuation_score", "chip_score", "momentum_score",
    "macro_score", "news_score", "composite_score", "core_score",
    "risk_score", "candidate_score", "entry_score", "volume_score",
    "vol_ratio", "buy_sell_ratio", "open_to_close_pct", "close_position",
    "final_score",   # 故意納入：讓 ML 有機會「至少打平」規則分數，門檻才公平
]
FEATURES_FROM_TECH = [
    "rsi14", "macd_hist", "distance_ma20",
    "return_5d", "return_20d", "atr14", "volatility_20d",
]
FEATURES_FROM_CHIP = [
    "foreign_net", "trust_net", "dealer_net",
    "margin_balance", "short_balance",
]

# 對照基準：規則系統的綜合分數。ML 至少要贏過直接用它排序，才算有加分。
BASELINE_COL = "final_score"
