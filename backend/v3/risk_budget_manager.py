"""
backend/v3/risk_budget_manager.py
V3-FIX-3：風險預算管理器
下單前必須先通過此模組檢查
"""
from __future__ import annotations
import json
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


# 預設風控參數
DEFAULT_LIMITS = {
    "single_stock_max_ratio":    0.15,
    "single_theme_max_ratio":    0.50,
    "single_strategy_max_ratio": 0.40,
    "min_cash_ratio":            0.10,
    "high_vol_stock_max_ratio":  0.08,
}

# 高波動股票清單（可定期更新）
HIGH_VOL_CODES = {"2303","3481","2409","2408","6669","2337","3706","6770"}


def _get_account_portfolio(account_id: int, trade_date: date, db) -> dict:
    """取得帳戶目前持倉與現金"""
    equity_row = db.execute(text("""
        SELECT total, cash FROM equity_curve
        WHERE strategy_id = :aid
          AND date = (SELECT MAX(date) FROM equity_curve WHERE strategy_id=:aid AND date<=:d)
    """), {"aid": account_id, "d": str(trade_date)}).fetchone()

    total = float(equity_row[0]) if equity_row else 200_000
    cash  = float(equity_row[1]) if equity_row and equity_row[1] else total * 0.3

    # 取目前持倉
    positions = db.execute(text("""
        SELECT code, shares, avg_cost FROM trade_logs
        WHERE account_id = :aid AND action='BUY'
          AND trade_date <= :d
        GROUP BY code
        HAVING SUM(CASE WHEN action='BUY' THEN shares ELSE -shares END) > 0
    """), {"aid": account_id, "d": str(trade_date)}).fetchall()

    holdings = {}
    for p in positions:
        code, shares, cost = p[0], float(p[1] or 0), float(p[2] or 0)
        price_row = db.execute(text("""
            SELECT close FROM ohlcv_daily
            WHERE code=:c AND trade_date<=:d AND close IS NOT NULL
            ORDER BY trade_date DESC LIMIT 1
        """), {"c": code, "d": str(trade_date)}).scalar()
        price = float(price_row or cost)
        holdings[code] = {"shares": shares, "value": shares * price}

    return {"total": total, "cash": cash, "holdings": holdings}


def _get_stock_theme(code: str, db) -> str:
    """取得股票所屬主題（簡化版）"""
    row = db.execute(text("""
        SELECT industry FROM stock_meta WHERE code=:c LIMIT 1
    """), {"c": code}).fetchone()
    if row and row[0]:
        industry = row[0]
        if any(k in industry for k in ["半導體","IC","積體電路"]): return "半導體"
        if any(k in industry for k in ["AI","人工智","伺服器"]): return "AI"
        if any(k in industry for k in ["PCB","電路板"]): return "PCB"
        if any(k in industry for k in ["金融","銀行","保險"]): return "金融"
        return industry[:4]
    return "其他"


def check_and_approve(
    account_id: int,
    code: str,
    order_amount: float,
    trade_date: date,
    strategy_id: int = None,
    risk_level: str = "medium",
) -> dict:
    """
    下單前風險檢查
    回傳：
    {
        "approved": bool,
        "allowed_amount": float,
        "blocked_reason": str or None,
        "adjustments": list[str],
    }
    """
    db = SessionLocal()
    try:
        portfolio = _get_account_portfolio(account_id, trade_date, db)
        total  = portfolio["total"]
        cash   = portfolio["cash"]
        holdings = portfolio["holdings"]
        theme  = _get_stock_theme(code, db)
        now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        limits = dict(DEFAULT_LIMITS)
        adjustments = []
        blocked_reason = None
        allowed_amount = order_amount

        # 高風險市場降低限額
        if risk_level == "high":
            limits["single_stock_max_ratio"]    = 0.10
            limits["min_cash_ratio"]             = 0.20
            limits["high_vol_stock_max_ratio"]   = 0.05
            adjustments.append("高風險市場，降低持倉上限")

        # 1. 最低現金比例
        cash_ratio = (cash - order_amount) / total if total > 0 else 0
        if cash_ratio < limits["min_cash_ratio"]:
            blocked_reason = f"現金比例不足（剩{cash/total*100:.1f}%，需{limits['min_cash_ratio']*100:.0f}%）"

        # 2. 單一股票上限
        current_stock_val = holdings.get(code, {}).get("value", 0)
        max_ratio = limits["high_vol_stock_max_ratio"] if code in HIGH_VOL_CODES \
                    else limits["single_stock_max_ratio"]
        new_ratio = (current_stock_val + order_amount) / total if total > 0 else 0
        if new_ratio > max_ratio and not blocked_reason:
            excess = (current_stock_val + order_amount) - (max_ratio * total)
            if excess >= order_amount:
                blocked_reason = f"{code} 已達單股上限（{max_ratio*100:.0f}%）"
            else:
                allowed_amount = order_amount - excess
                adjustments.append(f"縮減下單至 {allowed_amount:,.0f}（單股上限{max_ratio*100:.0f}%）")

        # 3. 單一主題上限
        theme_val = sum(h["value"] for c, h in holdings.items()
                        if _get_stock_theme(c, db) == theme)
        theme_ratio = (theme_val + allowed_amount) / total if total > 0 else 0
        if theme_ratio > limits["single_theme_max_ratio"] and not blocked_reason:
            excess = (theme_val + allowed_amount) - (limits["single_theme_max_ratio"] * total)
            if excess >= allowed_amount:
                blocked_reason = f"{theme} 主題已達上限（{limits['single_theme_max_ratio']*100:.0f}%）"
            else:
                allowed_amount -= excess
                adjustments.append(f"主題 {theme} 縮減至 {allowed_amount:,.0f}")

        # 計算各類曝險（供記錄用）
        theme_exp = {}
        for c, h in holdings.items():
            t = _get_stock_theme(c, db)
            theme_exp[t] = theme_exp.get(t, 0) + h["value"]

        approved = blocked_reason is None and allowed_amount > 0

        # 寫入 DB
        db.execute(text("""
            INSERT INTO risk_budget_status (
                decision_time, trade_date, account_id, code, order_action,
                requested_order_amount, allowed_order_amount, adjusted_position_size,
                current_stock_exposure, current_theme_exposure_json,
                min_cash_ratio, single_stock_max_ratio, single_theme_max_ratio,
                high_volatility_stock_max_ratio, risk_level, blocked_reason
            ) VALUES (
                :dt, :td, :aid, :code, 'BUY',
                :req, :alw, :adj,
                :cse, :cte,
                :mcr, :ssmr, :stmr, :hvmr, :rl, :br
            )
        """), {
            "dt": now, "td": str(trade_date), "aid": account_id, "code": code,
            "req": order_amount, "alw": allowed_amount if approved else 0,
            "adj": allowed_amount / total if total > 0 else 0,
            "cse": current_stock_val / total if total > 0 else 0,
            "cte": json.dumps({k: round(v/total,3) for k,v in theme_exp.items()} if total > 0 else {}),
            "mcr": limits["min_cash_ratio"],
            "ssmr": limits["single_stock_max_ratio"],
            "stmr": limits["single_theme_max_ratio"],
            "hvmr": limits["high_vol_stock_max_ratio"],
            "rl": risk_level,
            "br": blocked_reason,
        })
        db.commit()

        result = {
            "approved": approved,
            "allowed_amount": allowed_amount if approved else 0,
            "blocked_reason": blocked_reason,
            "adjustments": adjustments,
            "stock_theme": theme,
            "portfolio_total": total,
            "cash_remaining": cash - (allowed_amount if approved else 0),
        }
        if blocked_reason:
            logger.warning(f"[RISK] {code} 被擋：{blocked_reason}")
        elif adjustments:
            logger.info(f"[RISK] {code} 縮減下單：{adjustments}")
        return result

    except Exception as e:
        logger.error(f"[RISK] 檢查失敗 {code}: {e}")
        db.rollback()
        return {"approved": True, "allowed_amount": order_amount,
                "blocked_reason": None, "adjustments": [f"風控檢查異常（{e}），放行"]}
    finally:
        db.close()


def get_budget_status(account_id: int = None, trade_date: str = None) -> list[dict]:
    """查詢風控記錄"""
    db = SessionLocal()
    try:
        q = "SELECT * FROM risk_budget_status WHERE 1=1"
        params = {}
        if account_id: q += " AND account_id=:aid"; params["aid"] = account_id
        if trade_date: q += " AND trade_date=:td"; params["td"] = trade_date
        q += " ORDER BY created_at DESC LIMIT 200"
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","decision_time","trade_date","account_id","code","order_action",
                "requested_order_amount","allowed_order_amount","adjusted_position_size",
                "current_stock_exposure","current_theme_exposure_json","current_sector_exposure_json",
                "current_strategy_exposure_json","min_cash_ratio","single_stock_max_ratio",
                "single_theme_max_ratio","single_strategy_max_ratio","high_volatility_stock_max_ratio",
                "risk_level","blocked_reason","created_at"]
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            for k in ["current_theme_exposure_json","current_sector_exposure_json",
                      "current_strategy_exposure_json"]:
                try: d[k] = json.loads(d[k] or "{}")
                except: d[k] = {}
            result.append(d)
        return result
    finally:
        db.close()
