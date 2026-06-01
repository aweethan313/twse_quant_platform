"""
backend/utils/trading_day.py
交易日判斷 + 假日污染防護

核心問題：TWSE STOCK_DAY_ALL 端點永遠回傳「最近一個交易日」的快照，
不理會傳入的 date 參數。假日抓到的是上一交易日資料，若直接寫入會造成
僵屍列污染（close/volume 與前一日完全相同）。

解法（不需維護假日清單，自動偵測）：
1. 週末直接判定非交易日。
2. 平日：抓到資料後，與資料庫最新交易日的指標股（2330、0050）收盤價比對；
   若完全相同 → 今天 API 回的是舊資料（假日）→ 判定非交易日。
"""
from __future__ import annotations
from datetime import date
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

# 指標股：用來偵測「API 回傳的是不是舊資料」
_PROBE_CODES = ["2330", "0050", "2317"]


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def latest_clean_trade_date(db=None) -> str | None:
    """資料庫裡最新的交易日（假設歷史資料已清乾淨）。"""
    own = False
    if db is None:
        db = SessionLocal(); own = True
    try:
        return db.execute(text(
            "SELECT MAX(trade_date) FROM ohlcv_daily WHERE code GLOB '[0-9][0-9][0-9][0-9]'"
        )).scalar()
    finally:
        if own:
            db.close()


def is_fetched_data_stale(fetched_df, target_date: date, db=None) -> bool:
    """
    比對抓到的資料與資料庫最新交易日的指標股收盤價。
    若指標股收盤價完全相同 → 視為假日舊資料（stale）。
    """
    if fetched_df is None or fetched_df.empty:
        return True

    own = False
    if db is None:
        db = SessionLocal(); own = True
    try:
        last_td = latest_clean_trade_date(db)
        if not last_td or str(last_td) >= str(target_date):
            # 沒有歷史可比，或目標日不比最新日新 → 無法判斷，保守放行
            return False

        # 取得抓到的指標股收盤
        fetched = {}
        for _, r in fetched_df.iterrows():
            code = str(r.get("code", "")).strip()
            if code in _PROBE_CODES:
                fetched[code] = float(r.get("close")) if r.get("close") is not None else None

        if not fetched:
            return False  # 沒抓到指標股，無法判斷，放行

        # 取得資料庫最新交易日的指標股收盤
        rows = db.execute(text("""
            SELECT code, close FROM ohlcv_daily
            WHERE trade_date=:td AND code IN ('2330','0050','2317')
        """), {"td": last_td}).fetchall()
        stored = {r[0]: float(r[1]) for r in rows if r[1] is not None}

        # 比對：所有共同指標股收盤都相同 → stale
        common = set(fetched) & set(stored)
        if not common:
            return False
        all_same = all(
            fetched[c] is not None and abs(fetched[c] - stored[c]) < 1e-6
            for c in common
        )
        if all_same:
            logger.warning(
                f"[TRADING_DAY] {target_date} 抓到的指標股收盤與 {last_td} 完全相同"
                f"（{ {c: stored[c] for c in common} }）→ 判定為非交易日，跳過寫入"
            )
        return all_same
    finally:
        if own:
            db.close()


def should_run_for(target_date: date) -> tuple[bool, str]:
    """
    pipeline 入口判斷：今天該不該跑。
    回傳 (should_run, reason)。
    注意：這裡只做週末判斷；平日的假日偵測在抓到資料後用 is_fetched_data_stale。
    """
    if is_weekend(target_date):
        return False, f"{target_date} 是週末（weekday={target_date.weekday()}）"
    return True, "平日，待抓資料後再驗證是否為假日"

def is_trading_day(d: date, db=None) -> bool:
    """
    判斷某日是否為交易日。
    - 週末 → False
    - 資料庫已有當日 OHLCV → True（確實是交易日）
    - 其他（未來日/無資料）→ 僅用週末判斷（保守視為可能交易日）
    """
    if is_weekend(d):
        return False
    own = False
    if db is None:
        db = SessionLocal(); own = True
    try:
        n = db.execute(text("""
            SELECT COUNT(*) FROM ohlcv_daily
            WHERE trade_date=:d AND code GLOB '[0-9][0-9][0-9][0-9]'
        """), {"d": str(d)}).scalar()
        if n and n > 100:
            return True
        # 無資料：無法確定是假日還是還沒抓，平日保守回 True
        return True
    finally:
        if own:
            db.close()

