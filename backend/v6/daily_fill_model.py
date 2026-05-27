"""backend/v6/daily_fill_model.py
V6-4 Daily-only 成交模型（不使用分鐘資料）
V6-6 禁止當日先買後賣
"""
from __future__ import annotations
from datetime import date
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

# 費用常數
FEE_RATE  = 0.001425 * 0.38
TAX_RATE  = 0.003
MIN_FEE   = 20
SLIP_BPS  = 10  # 預設滑價 10bps


def get_next_trading_date(db, signal_date: str) -> str | None:
    """取 signal_date 後的下一個有效交易日"""
    # 優先用 trading_calendar
    try:
        r = db.execute(text("""
            SELECT MIN(trade_date) FROM trading_calendar
            WHERE trade_date > :d AND is_open=1
        """), {"d": signal_date}).scalar()
        if r: return str(r)
    except Exception:
        pass
    # fallback：用 ohlcv_daily
    r = db.execute(text("""
        SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > :d
    """), {"d": signal_date}).scalar()
    return str(r) if r else None


def simulate_daily_fill(
    code: str,
    signal_date: str,
    side: str,              # "BUY" or "SELL"
    shares: int,
    price_mode: str = "next_open",
    slippage_bps: float = SLIP_BPS,
    db=None,
) -> dict:
    """
    模擬日級成交（T+1 open）
    - signal_date: 產生訊號的日期（T）
    - fill_date: 下一個有效交易日（T+1）
    - price_mode: next_open / next_close / prev_close
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        fill_date = get_next_trading_date(db, signal_date)
        if not fill_date:
            return {"ok": False, "error": "找不到下一個交易日", "code": code}

        # 取 T+1 價格
        price_row = db.execute(text("""
            SELECT open, close FROM ohlcv_daily
            WHERE code=:c AND trade_date=:d
        """), {"c": code, "d": fill_date}).fetchone()

        raw_price = None
        actual_price_mode = price_mode
        fallback_reason = None
        is_estimated = 1

        if price_row:
            open_p = float(price_row[0] or 0)
            close_p = float(price_row[1] or 0)

            if price_mode == "next_open" and open_p > 0:
                raw_price = open_p
            elif open_p > 0:
                raw_price = open_p
                fallback_reason = "open_missing_using_next_open"
            elif close_p > 0:
                raw_price = close_p
                actual_price_mode = "next_close"
                fallback_reason = "open_missing_fallback_to_close"

        if not raw_price:
            # 最後 fallback：用 signal_date close
            prev = db.execute(text("""
                SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d
            """), {"c": code, "d": signal_date}).scalar()
            if prev and float(prev) > 0:
                raw_price = float(prev)
                actual_price_mode = "prev_close"
                fallback_reason = "no_next_day_price_fallback_to_signal_close"
            else:
                return {"ok": False, "error": f"無法取得 {code} {fill_date} 成交價"}

        # 計算成交價（含滑價）
        slip = slippage_bps / 10000
        if side == "BUY":
            fill_price = raw_price * (1 + slip)
        else:
            fill_price = raw_price * (1 - slip)

        gross = fill_price * shares
        fee = max(MIN_FEE, round(gross * FEE_RATE, 0))
        tax = round(gross * TAX_RATE, 0) if side == "SELL" else 0

        if side == "BUY":
            total_amount = gross + fee
        else:
            total_amount = gross - fee - tax

        return {
            "ok": True,
            "code": code,
            "signal_date": signal_date,
            "fill_date": fill_date,
            "side": side,
            "shares": shares,
            "raw_price": round(raw_price, 2),
            "fill_price": round(fill_price, 2),
            "price_mode": actual_price_mode,
            "fill_source": "daily_simulated",
            "is_estimated": is_estimated,
            "fallback_reason": fallback_reason,
            "fee": fee,
            "tax": tax,
            "total_amount": round(total_amount, 0),
        }
    finally:
        if close_db:
            db.close()


# ─────────────────────────────────
# V6-6: 當沖防護
# ─────────────────────────────────

def can_sell_without_day_trade_violation(
    account_id: int,
    code: str,
    fill_date: str,
    shares_to_sell: int,
    db=None,
) -> tuple[bool, str]:
    """
    檢查是否違反當沖規則
    - 同日先買後賣 → 禁止
    - 同日先賣後買 → 允許
    Returns: (allowed: bool, reason: str)
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # 今日是否有買進紀錄
        today_buy = db.execute(text("""
            SELECT SUM(shares) FROM paper_fills
            WHERE account_id=:aid AND code=:c
              AND execution_date=:d AND action='BUY'
        """), {"aid": account_id, "c": code, "d": fill_date}).scalar() or 0

        if today_buy > 0:
            return False, f"same_day_buy_then_sell_not_allowed（今日已買 {today_buy} 股）"

        return True, ""
    finally:
        if close_db:
            db.close()


def check_no_lookahead(signal_date: str, data_date: str) -> bool:
    """確認資料日期 <= signal_date（不偷看未來）"""
    return data_date <= signal_date
