"""
backend/signals/scorer_v2.py
S8 v2-5A 擴充分數系統 — stock_class 修正版

stock_class 分類邏輯：
  ETF_CORE        → 0050 / 006208（核心 ETF，不受短線賣出影響）
  ETF_INCOME      → 收益型 ETF（00878 / 00919 / 00929 / 00981A 等）
  CORE_LARGE_CAP  → TWSE50 成分股 或 長期流動性前 10%
  LARGE_LIQUID    → 流動性前 20%，不符合 CORE 條件
  LIQUID_MOMENTUM → 流動性足夠 + 趨勢向上 + risk_score <= 55
  SPECULATIVE_HOT → 短線過熱 / 高 risk / 題材熱股
  ILLIQUID_RISK   → 流動性過低
  NORMAL          → 其他

重要：watchlist 另設 is_watchlist 欄位，不影響 stock_class
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.database import SessionLocal

# ════════════════════════════════════════════════
# ETF 分類
# ════════════════════════════════════════════════

ETF_CORE_CODES = {"0050", "006208"}  # 台灣50 及其追蹤 ETF

ETF_INCOME_CODES = {
    "00878", "00919", "00929", "00934", "00940",
    "00713", "00878", "00900", "00907", "00915",
    "00918", "00930", "00936", "00939", "00944",
    "00981A", "0056",
}

# ════════════════════════════════════════════════
# TWSE 50 成分股（台灣 50 指數，約 2025 年）
# 這是相對穩定的大型藍籌股清單
# ════════════════════════════════════════════════

TWSE50_COMPONENTS = {
    "2330","2454","2303","3711","4938","2308","2382","2357","2395","3034",
    "6415","3008","2379","6669","2408","2409","3481","8046",
    "2412","4904","3045",
    "2882","2881","2891","2886","2884","2885","2887","2890","2892","5880","5871",
    "2317","2002","1301","1303","6505","1216","2912","2207",
    "2474","9904","9914","9910","2385","3231","6446","1101","1102",
}


def _is_etf(code: str) -> bool:
    """ETF 代碼特徵：以 0 開頭且長度 4~6"""
    return code.startswith("0") and 4 <= len(code) <= 6


def _sf(x, d=0.0) -> float:
    if x is None:
        return d
    try:
        v = float(x)
        return d if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return d


# ════════════════════════════════════════════════
# 技術指標輔助
# ════════════════════════════════════════════════

def _get_tech_data(code: str, score_date: date, db: Session, lookback: int = 65) -> Optional[dict]:
    rows = db.execute(text("""
        SELECT trade_date, open, high, low, close, volume
        FROM ohlcv_daily
        WHERE code = :code
          AND trade_date <= :d
          AND close IS NOT NULL AND volume IS NOT NULL AND volume > 0
        ORDER BY trade_date DESC LIMIT :n
    """), {"code": code, "d": score_date, "n": lookback}).fetchall()

    if not rows or len(rows) < 3:
        return None

    rows = list(reversed(rows))
    closes = [_sf(r[4]) for r in rows]
    opens  = [_sf(r[1]) for r in rows]
    highs  = [_sf(r[2]) for r in rows]
    lows   = [_sf(r[3]) for r in rows]
    vols   = [_sf(r[5]) for r in rows]
    n = len(rows)
    today = rows[-1]
    prev  = rows[-2] if n >= 2 else today

    close0     = _sf(today[4])
    open0      = _sf(today[1])
    high0      = _sf(today[2])
    low0       = _sf(today[3])
    vol0       = _sf(today[5])
    prev_close = _sf(prev[4])

    def ma(series, period):
        return sum(series[-period:]) / period if len(series) >= period else None

    ma5  = ma(closes, 5)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)

    avg_vol20 = ma(vols, 20) if len(vols) >= 20 else None
    volume_ratio = (vol0 / avg_vol20) if avg_vol20 and avg_vol20 > 0 else 1.0

    # RSI14
    rsi14 = None
    if n >= 15:
        gains, losses = [], []
        for i in range(n - 14, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        ag = sum(gains) / 14
        al = sum(losses) / 14
        rsi14 = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

    hl = high0 - low0
    if hl > 0:
        close_position = (close0 - low0) / hl
        upper_shadow   = (high0 - max(open0, close0)) / hl
        lower_shadow   = (min(open0, close0) - low0) / hl
    else:
        close_position = upper_shadow = lower_shadow = 0.0

    gap_pct      = (open0 / prev_close - 1.0) if prev_close > 0 else 0.0
    intraday_ret = (close0 / open0 - 1.0) if open0 > 0 else 0.0

    def ret_n(nd):
        if len(closes) > nd:
            base = closes[-(nd + 1)]
            return (closes[-1] / base - 1.0) if base > 0 else 0.0
        return None

    # avg_turnover20：千元為單位
    avg_turnover20 = None
    if len(rows) >= 20:
        tv = [_sf(rows[i][4]) * _sf(rows[i][5]) / 1000 for i in range(n - 20, n)]
        avg_turnover20 = sum(tv) / 20

    return {
        "close": close0, "open": open0, "high": high0, "low": low0,
        "vol": vol0, "prev_close": prev_close,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "avg_vol20": avg_vol20, "volume_ratio": volume_ratio,
        "rsi14": rsi14,
        "close_position": close_position,
        "upper_shadow": upper_shadow, "lower_shadow": lower_shadow,
        "gap_pct": gap_pct, "intraday_ret": intraday_ret,
        "ret5": ret_n(5), "ret10": ret_n(10),
        "ret20": ret_n(20), "ret60": ret_n(60),
        "avg_turnover20": avg_turnover20,
    }


# ════════════════════════════════════════════════
# 市場流動性分位快取（當天所有股票的排名基準）
# ════════════════════════════════════════════════

_market_turnover_cache: dict[date, dict] = {}


def _get_market_turnover_percentile(score_date: date, db: Session) -> dict:
    """
    取得當天所有股票的 avg_turnover20，
    回傳 {code: percentile_rank} 其中 rank=0~100
    """
    if score_date in _market_turnover_cache:
        return _market_turnover_cache[score_date]

    rows = db.execute(text("""
        SELECT code,
               AVG(close * volume / 1000) as avg_t
        FROM ohlcv_daily
        WHERE trade_date > date(:d, '-30 days')
          AND trade_date <= :d
          AND close IS NOT NULL AND volume IS NOT NULL AND volume > 0
        GROUP BY code
        HAVING COUNT(*) >= 15
    """), {"d": score_date}).fetchall()

    if not rows:
        _market_turnover_cache[score_date] = {}
        return {}

    vals = sorted([(r[0], _sf(r[1])) for r in rows], key=lambda x: x[1])
    n = len(vals)
    result = {code: round(i / n * 100, 1) for i, (code, _) in enumerate(vals)}
    _market_turnover_cache[score_date] = result
    return result


# ════════════════════════════════════════════════
# StockClassifier（修正版）
# ════════════════════════════════════════════════

def classify_stock(code, tech, risk_score, risk_flags, score_date, db):
    # 1. ETF 優先
    if code in ETF_CORE_CODES:   return "ETF_CORE"
    if code in ETF_INCOME_CODES: return "ETF_INCOME"
    if _is_etf(code):            return "NORMAL"

    # 2. 取流動性資料
    pct = _get_market_turnover_percentile(score_date, db)
    lp  = pct.get(code, 50.0)
    at  = (tech["avg_turnover20"] if tech and tech.get("avg_turnover20") else 0)

    # 3. 低流動性高風險
    if at < 3_000 or lp < 10:
        return "ILLIQUID_RISK"

    # 4. CORE_LARGE_CAP：嚴格只給 TWSE50 成分股，不靠流動性分位晉升
    if code in TWSE50_COMPONENTS:
        return "CORE_LARGE_CAP"

    # 5. LARGE_LIQUID：流動性前20% 或 日均5千萬以上（但不是 CORE）
    if lp >= 80 or at >= 50_000:
        return "LARGE_LIQUID"

    if not tech:
        return "NORMAL"

    ratio = tech["volume_ratio"]
    ret5  = tech.get("ret5") or 0.0
    ret10 = tech.get("ret10") or 0.0
    close = tech["close"]
    ma20  = tech["ma20"]
    ma60  = tech["ma60"]

    # 6. SPECULATIVE_HOT：短線過熱
    if (ret5 > 0.15 or ret10 > 0.25 or ratio >= 2.5
            or "consecutive_limit_up" in risk_flags
            or "limit_up_opened" in risk_flags
            or risk_score >= 65):
        return "SPECULATIVE_HOT"

    # 7. LIQUID_MOMENTUM：流動性足夠 + 趨勢健康 + 風險可控
    if (at >= 10_000
            and ma20 and close > ma20
            and ma60 and ma20 >= ma60
            and risk_score <= 55):
        return "LIQUID_MOMENTUM"

    return "NORMAL"


def compute_volume_score(tech: dict) -> float:
    if not tech:
        return 50.0
    ratio    = tech["volume_ratio"]
    intraday = tech["intraday_ret"]
    cp       = tech["close_position"]
    us       = tech["upper_shadow"]

    score = 50.0
    if ratio >= 2.0 and intraday > 0.01 and cp >= 0.6 and us < 0.35:
        score = min(95, 50 + ratio * 12)
    elif ratio >= 1.5 and intraday > 0:
        score = min(80, 50 + ratio * 8)
    elif ratio >= 2.0 and us >= 0.4:
        score = max(20, 50 - us * 50)
    elif ratio >= 2.0 and intraday < -0.01:
        score = max(10, 50 - ratio * 10)
    elif ratio < 0.5:
        score = max(20, 50 - (1 - ratio) * 30)
    elif ratio < 0.8:
        score = max(35, 50 - (1 - ratio) * 20)
    else:
        score = 50.0 + intraday * 200

    return round(float(max(0, min(100, score))), 2)


# ════════════════════════════════════════════════
# EntryScorer
# ════════════════════════════════════════════════

def compute_entry_score(tech: dict) -> float:
    if not tech:
        return 50.0
    score    = 60.0
    close    = tech["close"]
    ma20     = tech["ma20"]
    ma60     = tech["ma60"]
    rsi14    = tech["rsi14"]
    ratio    = tech["volume_ratio"]
    intraday = tech["intraday_ret"]
    us       = tech["upper_shadow"]
    gap      = tech["gap_pct"]
    cp       = tech["close_position"]
    ret5     = tech.get("ret5") or 0.0

    if ma20 and close > ma20:
        score += 5
    if ma20 and ma60 and ma20 >= ma60:
        score += 5
    if rsi14 is not None:
        if 45 <= rsi14 <= 65:
            score += 8
        elif 35 <= rsi14 < 45:
            score += 5
        elif rsi14 > 75:
            score -= 10
    if ratio >= 1.5 and intraday > 0.01 and cp >= 0.6:
        score += 8
    elif ratio >= 1.0 and intraday > 0:
        score += 3
    if ma20 and ma20 > 0:
        dist = (close - ma20) / ma20
        if   dist > 0.20: score -= 15
        elif dist > 0.15: score -= 10
        elif dist > 0.10: score -= 5
        elif dist < -0.05: score -= 5
    if gap > 0.03 and intraday < -0.02:
        score -= 12
    if us >= 0.45:
        score -= 10
    elif us >= 0.35:
        score -= 5
    if ratio >= 2.0 and intraday < -0.01:
        score -= 15
    if ret5 > 0.15:
        score -= 12
    elif ret5 > 0.10:
        score -= 6

    return round(float(max(0, min(100, score))), 2)


# ════════════════════════════════════════════════
# RiskScorer
# ════════════════════════════════════════════════

RISK_FLAG_WEIGHTS = {
    "short_term_overheat":          10,
    "too_far_from_ma":               8,
    "rsi_overheated":               12,
    "consecutive_limit_up":         20,
    "high_volume_upper_shadow":     18,
    "gap_up_fade":                  14,
    "high_volume_black_candle":     18,
    "limit_up_opened":              16,
    "hot_money_day_trade_risk":     14,
    "price_volume_divergence":      10,
    "institutions_sell_retail_buy": 16,
}

MAJOR_FLAGS = {
    "high_volume_upper_shadow",
    "high_volume_black_candle",
    "limit_up_opened",
    "institutions_sell_retail_buy",
}


def compute_risk_score_and_flags(
    tech: dict, code: str, score_date: date, db: Session,
    chip_net: float = None
) -> tuple[float, list[str]]:
    if not tech:
        return 30.0, []

    flags      = []
    close      = tech["close"]
    open0      = tech["open"]
    high0      = tech["high"]
    ma20       = tech["ma20"]
    ma60       = tech["ma60"]
    rsi14      = tech["rsi14"]
    ratio      = tech["volume_ratio"]
    us         = tech["upper_shadow"]
    gap        = tech["gap_pct"]
    intra      = tech["intraday_ret"]
    cp         = tech["close_position"]
    ret5       = tech.get("ret5") or 0.0
    ret10      = tech.get("ret10") or 0.0
    ret20      = tech.get("ret20") or 0.0
    prev_close = tech["prev_close"]

    if ret5 > 0.15 or ret10 > 0.25 or ret20 > 0.40:
        flags.append("short_term_overheat")

    if ma20 and ma20 > 0 and (close - ma20) / ma20 > 0.15:
        flags.append("too_far_from_ma")
    elif ma60 and ma60 > 0 and (close - ma60) / ma60 > 0.30:
        flags.append("too_far_from_ma")

    if rsi14 is not None and rsi14 > 75:
        flags.append("rsi_overheated")

    try:
        lrows = db.execute(text("""
            SELECT change_pct FROM ohlcv_daily
            WHERE code=:c AND trade_date <= :d AND change_pct IS NOT NULL
            ORDER BY trade_date DESC LIMIT 2
        """), {"c": code, "d": score_date}).fetchall()
        if len(lrows) >= 2 and all(_sf(r[0]) >= 9.5 for r in lrows):
            flags.append("consecutive_limit_up")
    except Exception:
        pass

    if ratio >= 2.0 and us >= 0.45 and cp <= 0.55:
        flags.append("high_volume_upper_shadow")

    if gap > 0.03 and intra < -0.02 and ratio >= 1.5:
        flags.append("gap_up_fade")

    if close < open0 and ratio >= 2.0 and cp <= 0.4:
        flags.append("high_volume_black_candle")

    if prev_close > 0:
        high_pct  = (high0 / prev_close - 1) * 100
        close_pct = (close / prev_close - 1) * 100
        if high_pct >= 9.5 and close_pct < 8.0 and ratio >= 2.0:
            flags.append("limit_up_opened")

    is_etf_code = _is_etf(code)
    if not is_etf_code and intra > 0.07 and ratio >= 2.5 and us >= 0.3:
        flags.append("hot_money_day_trade_risk")

    if chip_net is not None and chip_net < -500 and ratio >= 2.0 and intra > 0.03:
        flags.append("institutions_sell_retail_buy")

    base = 20.0
    for flag in flags:
        base += RISK_FLAG_WEIGHTS.get(flag, 5)

    return round(min(100, base), 2), flags


# ════════════════════════════════════════════════
# 複合分數
# ════════════════════════════════════════════════

def compute_candidate_score(scores: dict) -> float:
    return round(
        _sf(scores.get("fundamental_score")) * 0.20 +
        _sf(scores.get("valuation_score"))   * 0.15 +
        _sf(scores.get("chip_score"))         * 0.20 +
        _sf(scores.get("momentum_score"))     * 0.15 +
        _sf(scores.get("volume_score"))       * 0.10 +
        _sf(scores.get("macro_score"))        * 0.10 +
        _sf(scores.get("news_score"))         * 0.10,
        2
    )


def compute_core_score(scores: dict) -> float:
    return round(
        _sf(scores.get("fundamental_score")) * 0.30 +
        _sf(scores.get("valuation_score"))   * 0.20 +
        _sf(scores.get("chip_score"))         * 0.15 +
        _sf(scores.get("momentum_score"))     * 0.10 +
        _sf(scores.get("volume_score"))       * 0.05 +
        _sf(scores.get("macro_score"))        * 0.15 +
        _sf(scores.get("news_score"))         * 0.05,
        2
    )


def compute_final_score(candidate: float, entry: float, risk: float) -> float:
    penalty = max(0.0, risk - 40) * 0.5
    return round(max(0, min(100, candidate * 0.6 + entry * 0.4 - penalty)), 2)


def compute_final_action(
    candidate: float, entry: float, risk: float,
    stock_class: str, risk_flags: list[str]
) -> str:
    has_major = bool(set(risk_flags) & MAJOR_FLAGS)

    # ETF 不給 BUY/SELL 訊號，只給 HOLD
    if stock_class in ("ETF_CORE", "ETF_INCOME"):
        return "HOLD"

    # ILLIQUID_RISK 一律降級
    if stock_class == "ILLIQUID_RISK":
        return "AVOID_CHASE" if candidate >= 60 else "HOLD"

    # 主要邏輯
    if candidate >= 60 and entry >= 55 and risk <= 45 and not has_major:
        return "BUY"
    if candidate >= 65 and (risk >= 60 or has_major):
        return "AVOID_CHASE"
    if candidate >= 65 and (entry < 55 or risk > 45):
        return "WATCH"
    return "HOLD"


# ════════════════════════════════════════════════
# 批次計算
# ════════════════════════════════════════════════

def compute_extended_scores(codes: list[str], score_date: date):
    db = SessionLocal()
    updated = 0

    # 預先清除當天的市場分位快取
    _market_turnover_cache.pop(score_date, None)

    for code in codes:
        try:
            row = db.execute(text("""
                SELECT fundamental_score, valuation_score, chip_score,
                       momentum_score, macro_score, news_score
                FROM daily_scores
                WHERE code=:code AND score_date=:d
            """), {"code": code, "d": score_date}).fetchone()

            if not row:
                continue

            base_scores = {
                "fundamental_score": _sf(row[0], 50),
                "valuation_score":   _sf(row[1], 50),
                "chip_score":        _sf(row[2], 50),
                "momentum_score":    _sf(row[3], 50),
                "macro_score":       _sf(row[4], 50),
                "news_score":        _sf(row[5], 50),
            }

            tech = _get_tech_data(code, score_date, db)

            chip_net_raw = db.execute(text("""
                SELECT COALESCE(foreign_net,0)+COALESCE(trust_net,0)+COALESCE(dealer_net,0)
                FROM chip_daily WHERE code=:c
                AND trade_date=(SELECT MAX(trade_date) FROM chip_daily
                                WHERE code=:c AND trade_date<=:d)
            """), {"c": code, "d": score_date}).scalar()
            chip_net = _sf(chip_net_raw)

            volume_score           = compute_volume_score(tech)
            entry_score            = compute_entry_score(tech)
            risk_score, risk_flags = compute_risk_score_and_flags(
                tech, code, score_date, db, chip_net)

            all_scores = {**base_scores, "volume_score": volume_score}
            candidate_score = compute_candidate_score(all_scores)
            core_score      = compute_core_score(all_scores)
            final_score     = compute_final_score(candidate_score, entry_score, risk_score)
            stock_class     = classify_stock(code, tech, risk_score, risk_flags, score_date, db)
            final_action    = compute_final_action(
                candidate_score, entry_score, risk_score, stock_class, risk_flags)

            db.execute(text("""
                UPDATE daily_scores SET
                    volume_score    = :vs,
                    candidate_score = :cs,
                    entry_score     = :es,
                    risk_score      = :rs,
                    risk_flags      = :rf,
                    final_score     = :fs,
                    final_action    = :fa,
                    core_score      = :cor,
                    stock_class     = :sc
                WHERE code=:code AND score_date=:d
            """), {
                "vs": volume_score,  "cs": candidate_score,
                "es": entry_score,   "rs": risk_score,
                "rf": json.dumps(risk_flags, ensure_ascii=False),
                "fs": final_score,   "fa": final_action,
                "cor": core_score,   "sc": stock_class,
                "code": code,        "d": score_date,
            })
            updated += 1

        except Exception as e:
            logger.warning(f"[SCORER_V2] {code}: {e}")

        if updated % 200 == 0 and updated > 0:
            db.commit()

    db.commit()
    db.close()
    logger.success(f"[SCORER_V2] {score_date} 擴充分數完成，共 {updated} 檔")
