"""
backend/v3/realistic_trade_fills.py
V3-FIX-4：真實成交模型
- 日K策略：收盤產生訊號 → 最早下一交易日 open 成交
- 禁止當沖：同股票買進當天不得賣出今天買進的股數
- 0050 不被短線策略強制賣出
- 費稅記錄
"""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

COMMISSION_RATE = 0.001425   # 手續費
TAX_RATE        = 0.003      # 證交稅（賣出）
SLIPPAGE_LONG   = 0.001      # 整股買進滑價
SLIPPAGE_SHORT  = 0.002      # 零股買進滑價

CORE_ETF_CODES = {"0050", "006208"}


def _get_next_trading_day_open(code: str, signal_date: date, db) -> tuple[date | None, float | None]:
    """取得訊號日之後第一個交易日的開盤價"""
    row = db.execute(text("""
        SELECT trade_date, open FROM ohlcv_daily
        WHERE code=:c AND trade_date > :d
          AND open IS NOT NULL AND open > 0
        ORDER BY trade_date LIMIT 1
    """), {"c": code, "d": str(signal_date)}).fetchone()
    if row:
        return row[0], float(row[1])
    return None, None


def _get_avg_volume(code: str, ref_date: date, db, days: int = 20) -> float:
    """取得近 N 日平均成交量"""
    rows = db.execute(text("""
        SELECT AVG(volume) FROM ohlcv_daily
        WHERE code=:c AND trade_date <= :d
          AND trade_date > date(:d, :offset)
          AND volume IS NOT NULL
    """), {"c": code, "d": str(ref_date), "offset": f"-{days} days"}).fetchone()
    return float(rows[0] or 0)


def _check_no_day_trade(account_id: int, code: str, fill_date: date,
                         requested_action: str, db) -> tuple[bool, str]:
    """
    禁止當沖檢查：
    如果今天已買進該股，不得賣出今天買進的股數
    """
    if requested_action.lower() != "sell":
        return True, ""

    today_buy = db.execute(text("""
        SELECT COALESCE(SUM(lots), 0) FROM realistic_trade_fills
        WHERE account_id=:aid AND code=:code
          AND action='buy' AND fill_time LIKE :d
          AND execution_status='FILLED'
    """), {"aid": account_id, "code": code, "d": str(fill_date) + "%"}).scalar()

    if today_buy and float(today_buy) > 0:
        return False, f"禁止當沖：今日已買進 {today_buy} 股，不可同日賣出"
    return True, ""


def _check_core_etf(code: str, strategy_id: int, action: str) -> tuple[bool, str]:
    """0050 不被短線策略強制賣出"""
    if code in CORE_ETF_CODES and action.lower() == "sell":
        return False, f"{code} 為核心 ETF，不允許短線策略強制賣出"
    return True, ""


def process_fill(
    account_id: int,
    strategy_id: int,
    code: str,
    action: str,          # 'buy' / 'sell'
    signal_date: date,
    requested_shares: float,
    signal_price: float = None,
    is_fractional: bool = False,
) -> dict:
    """
    處理一筆交易請求，回傳成交結果
    日K策略：signal_date 產生訊號 → 下一交易日 open 成交
    """
    db = SessionLocal()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    signal_time   = str(signal_date) + " 15:00:00"
    decision_time = str(signal_date) + " 15:30:00"

    result = {
        "account_id": account_id,
        "strategy_id": strategy_id,
        "code": code,
        "action": action,
        "signal_time": signal_time,
        "decision_time": decision_time,
        "order_time": None,
        "fill_time": None,
        "requested_shares": requested_shares,
        "filled_shares": 0,
        "fill_price": None,
        "reference_price": signal_price,
        "slippage": 0,
        "fee": 0,
        "tax": 0,
        "execution_status": "PENDING",
        "execution_reason": "",
    }

    try:
        # 1. 核心 ETF 保護
        ok, reason = _check_core_etf(code, strategy_id, action)
        if not ok:
            result["execution_status"] = "BLOCKED_BY_CORE_ETF_RULE"
            result["execution_reason"] = reason
            _write_fill(result, db)
            return result

        # 2. 取下一交易日 open
        fill_date, open_price = _get_next_trading_day_open(code, signal_date, db)
        if not fill_date or not open_price:
            result["execution_status"] = "MISSING_FILL_DATA"
            result["execution_reason"] = f"{signal_date} 之後無交易資料"
            _write_fill(result, db)
            return result

        order_time = str(fill_date) + " 09:00:00"
        fill_time  = str(fill_date) + " 09:05:00"
        result["order_time"] = order_time
        result["fill_time"]  = fill_time

        # 3. 禁止當沖
        ok, reason = _check_no_day_trade(account_id, code, fill_date, action, db)
        if not ok:
            result["execution_status"] = "BLOCKED_BY_NO_DAY_TRADE"
            result["execution_reason"] = reason
            _write_fill(result, db)
            return result

        # 4. 計算滑價
        slippage_rate = SLIPPAGE_SHORT if is_fractional else SLIPPAGE_LONG
        if action.lower() == "buy":
            fill_price = open_price * (1 + slippage_rate)
        else:
            fill_price = open_price * (1 - slippage_rate)
        fill_price = round(fill_price, 2)
        slippage = round(abs(fill_price - open_price) * requested_shares, 2)

        # 5. 成交量限制（零股）
        avg_vol = _get_avg_volume(code, fill_date, db)
        filled_shares = requested_shares
        if is_fractional and avg_vol > 0:
            # 零股：最多用當日成交量的 1%
            max_fill = avg_vol * 0.01
            if requested_shares > max_fill:
                filled_shares = max(1, int(max_fill))
                result["execution_reason"] = f"零股量不足，Partial Fill: {filled_shares}/{requested_shares:.0f}"

        # 6. 費稅
        amount = filled_shares * fill_price
        fee = round(amount * COMMISSION_RATE, 2)
        tax = round(amount * TAX_RATE, 2) if action.lower() == "sell" else 0

        result.update({
            "filled_shares": filled_shares,
            "fill_price": fill_price,
            "slippage": slippage,
            "fee": fee,
            "tax": tax,
            "execution_status": "PARTIAL_FILLED" if filled_shares < requested_shares else "FILLED",
            "execution_reason": result["execution_reason"] or f"下一日 open {open_price} 成交",
        })

        _write_fill(result, db)
        logger.info(f"[FILL] {code} {action} {filled_shares}股 @ {fill_price} "
                    f"({fill_date}) fee={fee} tax={tax}")
        return result

    except Exception as e:
        logger.error(f"[FILL] 失敗 {code}: {e}")
        result["execution_status"] = "REJECTED"
        result["execution_reason"] = str(e)
        _write_fill(result, db)
        return result
    finally:
        db.close()


def _write_fill(result: dict, db):
    try:
        db.execute(text("""
            INSERT INTO realistic_trade_fills (
                account_id, strategy_id, code, action,
                signal_time, decision_time, order_time, fill_time,
                requested_shares, filled_shares, fill_price, reference_price,
                slippage, fee, tax, execution_status, execution_reason
            ) VALUES (
                :aid, :sid, :code, :action,
                :st, :dt, :ot, :ft,
                :rs, :fs, :fp, :rp,
                :slip, :fee, :tax, :es, :er
            )
        """), {
            "aid": result["account_id"], "sid": result["strategy_id"],
            "code": result["code"], "action": result["action"],
            "st": result["signal_time"], "dt": result["decision_time"],
            "ot": result["order_time"], "ft": result["fill_time"],
            "rs": result["requested_shares"], "fs": result["filled_shares"],
            "fp": result["fill_price"], "rp": result["reference_price"],
            "slip": result["slippage"], "fee": result["fee"], "tax": result["tax"],
            "es": result["execution_status"], "er": result["execution_reason"],
        })
        db.commit()
    except Exception as e:
        logger.warning(f"[FILL] 寫入失敗: {e}")
        db.rollback()


def get_fills(account_id: int = None, code: str = None,
              start_date: str = None, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM realistic_trade_fills WHERE 1=1"
        params = {}
        if account_id: q += " AND account_id=:aid"; params["aid"] = account_id
        if code:       q += " AND code=:code";      params["code"] = code
        if start_date: q += " AND signal_time>=:sd"; params["sd"] = start_date
        q += " ORDER BY id DESC LIMIT :limit"; params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","account_id","strategy_id","code","action",
                "signal_time","decision_time","order_time","fill_time",
                "requested_shares","filled_shares","fill_price","reference_price",
                "slippage","fee","tax","execution_status","execution_reason","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


def verify_no_lookahead(fills: list[dict]) -> list[str]:
    """驗證沒有偷看未來（fill_time 必須 > signal_time）"""
    violations = []
    for f in fills:
        if f.get("signal_time") and f.get("fill_time"):
            if f["fill_time"] <= f["signal_time"]:
                violations.append(
                    f"{f['code']}: fill_time {f['fill_time']} <= signal_time {f['signal_time']}"
                )
    return violations
