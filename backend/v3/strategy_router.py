"""
backend/v3/strategy_router.py
V3-FIX-2：策略路由器
根據市場環境決定啟用哪些策略、降權哪些策略
"""
from __future__ import annotations
import json
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


STRATEGY_PROFILES = {
    1: {"name": "動能突破", "risk_tolerance": "high",   "market_fit": ["bullish"]},
    2: {"name": "強勢題材", "risk_tolerance": "high",   "market_fit": ["bullish"]},
    3: {"name": "趨勢續抱", "risk_tolerance": "medium", "market_fit": ["bullish", "neutral"]},
    4: {"name": "均值回歸", "risk_tolerance": "low",    "market_fit": ["neutral", "bearish"]},
    5: {"name": "品質防守", "risk_tolerance": "low",    "market_fit": ["neutral", "bearish"]},
    7: {"name": "ETF核心",  "risk_tolerance": "low",    "market_fit": ["bullish", "neutral", "bearish"]},
}


def _get_market_context(trade_date: date, db) -> dict:
    """取得最近的市場環境資料"""
    row = db.execute(text("""
        SELECT trend_regime, trend_regime, 'medium',
               COALESCE(breadth_score, 50), COALESCE(ai_theme_score, 50)
        FROM market_context_daily
        WHERE context_date <= :d
        ORDER BY context_date DESC LIMIT 1
    """), {"d": trade_date}).fetchone()

    if row:
        regime = (row[0] or "neutral").lower()
        if "bull" in regime or "up" in regime or "strong" in regime:
            trend = "bullish"
        elif "bear" in regime or "down" in regime or "weak" in regime:
            trend = "bearish"
        else:
            trend = "neutral"
        breadth = float(row[3] or 50)
        risk = "low" if breadth >= 60 else "high" if breadth <= 35 else "medium"
        return {
            "market_trend": trend,
            "tech_trend":   trend,
            "risk_level":   risk,
            "macro_score":  breadth,
            "ai_theme":     float(row[4] or 50),
        }

    # fallback：用 0050 走勢估算
    etf_rows = db.execute(text("""
        SELECT close FROM ohlcv_daily
        WHERE code='0050' AND trade_date <= :d
          AND close IS NOT NULL
        ORDER BY trade_date DESC LIMIT 65
    """), {"d": trade_date}).fetchall()

    if len(etf_rows) >= 60:
        closes = [float(r[0]) for r in reversed(etf_rows)]
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        last = closes[-1]
        if last > ma20 * 1.02 and ma20 > ma60:
            trend = "bullish"
        elif last < ma20 * 0.98 or ma20 < ma60 * 0.98:
            trend = "bearish"
        else:
            trend = "neutral"
        return {"market_trend": trend, "tech_trend": trend,
                "risk_level": "low" if trend == "bullish" else "high" if trend == "bearish" else "medium",
                "macro_score": 50, "ai_theme": 50}

    return {"market_trend": "neutral", "tech_trend": "neutral",
            "risk_level": "medium", "macro_score": 50, "ai_theme": 50}


def _get_theme_strength(trade_date: date, db) -> dict:
    """取得主題熱度"""
    rows = db.execute(text("""
        SELECT theme_name, score FROM theme_trend_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM theme_trend_daily WHERE trade_date <= :d)
        ORDER BY score DESC LIMIT 10
    """), {"d": trade_date}).fetchall()
    return {r[0]: float(r[1]) for r in rows} if rows else {}


def compute_router(trade_date: date) -> dict:
    """
    計算當天的策略路由決策
    回傳：enabled_strategies, disabled_strategies, weights, position_multiplier
    """
    db = SessionLocal()
    try:
        ctx = _get_market_context(trade_date, db)
        themes = _get_theme_strength(trade_date, db)

        market_trend = ctx["market_trend"]
        risk_level   = ctx["risk_level"]
        ai_strength  = themes.get("AI", ctx["ai_theme"])
        semi_strength = themes.get("半導體", 50)

        enabled, disabled, weights = [], [], {}
        reasons = []

        # 根據市場環境決定策略
        for sid, profile in STRATEGY_PROFILES.items():
            if market_trend in profile["market_fit"]:
                enabled.append(sid)
            else:
                disabled.append(sid)
                reasons.append(f"S{sid}({profile['name']})不適合{market_trend}市場")

        # position_multiplier
        if market_trend == "bullish" and risk_level == "low":
            position_multiplier = 1.0
            reasons.append("多頭低風險，滿倉")
        elif market_trend == "bullish":
            position_multiplier = 0.85
            reasons.append("多頭但有風險，略降倉")
        elif market_trend == "neutral":
            position_multiplier = 0.65
            reasons.append("盤整，保守配置")
        else:  # bearish
            position_multiplier = 0.35
            reasons.append("空頭，大幅降倉")

        # 策略權重
        for sid in enabled:
            base_w = 1.0
            if market_trend == "bullish" and STRATEGY_PROFILES[sid]["risk_tolerance"] == "high":
                base_w = 1.2
            elif market_trend == "bearish" and STRATEGY_PROFILES[sid]["risk_tolerance"] == "low":
                base_w = 1.3
            weights[sid] = round(base_w, 2)

        # 主題調整
        sector_adj = {}
        theme_adj  = {}
        if ai_strength >= 65:
            theme_adj["AI"]   = 1.2
            theme_adj["半導體"] = 1.15
            reasons.append(f"AI主題強（{ai_strength:.0f}），提高相關權重")
        if semi_strength >= 65:
            sector_adj["半導體"] = 1.15
        if risk_level == "high":
            theme_adj["題材股"] = 0.5
            reasons.append("高風險環境，題材股降權")

        # 寫入 DB
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(text("""
            INSERT INTO strategy_router_decisions (
                decision_time, trade_date, market_trend, tech_trend,
                semiconductor_trend, ai_theme_strength, risk_level,
                enabled_strategies, disabled_strategies, strategy_weight_json,
                position_multiplier, sector_weight_adjustments_json,
                theme_weight_adjustments_json, reason
            ) VALUES (
                :dt, :td, :mt, :tt, :st, :ai, :rl,
                :en, :dis, :sw, :pm, :sa, :ta, :reason
            )
        """), {
            "dt": now, "td": str(trade_date), "mt": market_trend,
            "tt": ctx["tech_trend"], "st": str(semi_strength),
            "ai": ai_strength, "rl": risk_level,
            "en":  json.dumps(enabled),
            "dis": json.dumps(disabled),
            "sw":  json.dumps(weights),
            "pm":  position_multiplier,
            "sa":  json.dumps(sector_adj),
            "ta":  json.dumps(theme_adj),
            "reason": "；".join(reasons),
        })
        db.commit()

        result = {
            "trade_date": str(trade_date),
            "market_trend": market_trend,
            "risk_level": risk_level,
            "ai_theme_strength": ai_strength,
            "enabled_strategies": enabled,
            "disabled_strategies": disabled,
            "strategy_weights": weights,
            "position_multiplier": position_multiplier,
            "sector_adjustments": sector_adj,
            "theme_adjustments": theme_adj,
            "reasons": reasons,
        }
        logger.info(f"[ROUTER] {trade_date} market={market_trend} pm={position_multiplier} "
                    f"enabled={enabled}")
        return result

    except Exception as e:
        logger.error(f"[ROUTER] 計算失敗: {e}")
        db.rollback()
        return {"trade_date": str(trade_date), "market_trend": "neutral",
                "risk_level": "medium", "enabled_strategies": list(STRATEGY_PROFILES.keys()),
                "disabled_strategies": [], "position_multiplier": 0.65,
                "strategy_weights": {}, "reasons": [f"fallback: {e}"]}
    finally:
        db.close()


def get_latest_router(trade_date: date = None) -> dict:
    """取得最近一筆路由決策"""
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_router_decisions"
        params = {}
        if trade_date:
            q += " WHERE trade_date <= :d"
            params["d"] = str(trade_date)
        q += " ORDER BY created_at DESC LIMIT 1"
        row = db.execute(text(q), params).fetchone()
        if not row:
            return compute_router(trade_date or date.today())

        cols = ["id","decision_time","trade_date","market_trend","tech_trend",
                "semiconductor_trend","ai_theme_strength","risk_level",
                "enabled_strategies","disabled_strategies","strategy_weight_json",
                "position_multiplier","sector_weight_adjustments_json",
                "theme_weight_adjustments_json","reason","created_at"]
        d = dict(zip(cols, row))
        for k in ["enabled_strategies","disabled_strategies","strategy_weight_json",
                  "sector_weight_adjustments_json","theme_weight_adjustments_json"]:
            try: d[k] = json.loads(d[k] or "[]")
            except: pass
        return d
    finally:
        db.close()


def is_strategy_enabled(strategy_id: int, trade_date: date = None) -> tuple[bool, float]:
    """
    檢查策略是否啟用，回傳 (is_enabled, position_multiplier)
    """
    router = get_latest_router(trade_date)
    enabled = router.get("enabled_strategies", list(STRATEGY_PROFILES.keys()))
    pm = float(router.get("position_multiplier", 0.65))
    return (strategy_id in enabled), pm
