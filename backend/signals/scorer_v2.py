"""
backend/signals/scorer_v2.py
S8 v2-5A 分數擴充：
  - VolumeScorer        → volume_score
  - EntryScorer         → entry_score
  - RiskScorer          → risk_score, risk_flags
  - StockClassifier     → stock_class
  - compute_candidate_score / compute_core_score / compute_final_score
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, DailyScore
from config.settings import settings

# ════════════════════════════════════════════════
# 核心股池
# ════════════════════════════════════════════════
CORE_POOL = {
    "0050", "00981A", "2330", "2454", "2308",
    "3711", "2383", "2449", "3037", "3189", "6205",
}


def _sf(x, d=0.0) -> float:
    """safe float"""
    if x is None:
        return d
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return d
        return v
    except Exception:
        return d


# ════════════════════════════════════════════════
# 技術指標輔助
# ════════════════════════════════════════════════

def _get_tech_data(code: str, score_date: date, db: Session, lookback: int = 65) -> Optional[dict]:
    """
    取得最近 lookback 天的 OHLCV，計算技術指標。
    只使用 score_date 以前（含）的資料，不偷看未來。
    """
    rows = db.execute(text("""
        SELECT trade_date, open, high, low, close, volume
        FROM ohlcv_daily
        WHERE code = :code
          AND trade_date <= :d
          AND close IS NOT NULL
          AND volume IS NOT NULL
          AND volume > 0
        ORDER BY trade_date DESC
        LIMIT :n
    """), {"code": code, "d": score_date, "n": lookback}).fetchall()

    if not rows or len(rows) < 3:
        return None

    rows = list(reversed(rows))  # 由舊到新
    closes = [_sf(r[4]) for r in rows]
    opens  = [_sf(r[1]) for r in rows]
    highs  = [_sf(r[2]) for r in rows]
    lows   = [_sf(r[3]) for r in rows]
    vols   = [_sf(r[5]) for r in rows]

    n = len(rows)
    today = rows[-1]
    prev  = rows[-2] if n >= 2 else today

    close0 = _sf(today[4])
    open0  = _sf(today[1])
    high0  = _sf(today[2])
    low0   = _sf(today[3])
    vol0   = _sf(today[5])
    prev_close = _sf(prev[4])

    # MA5, MA20, MA60
    def ma(series, period):
        if len(series) < period:
            return None
        return sum(series[-period:]) / period

    ma5  = ma(closes, 5)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)

    # Volume avg 20
    avg_vol20 = ma(vols, 20) if len(vols) >= 20 else None
    volume_ratio = (vol0 / avg_vol20) if avg_vol20 and avg_vol20 > 0 else 1.0

    # RSI14
    rsi14 = None
    if n >= 15:
        gains, losses = [], []
        for i in range(n - 14, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(-diff)
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0
        if avg_loss == 0:
            rsi14 = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi14 = 100 - 100 / (1 + rs)

    # Candle structure
    hl = high0 - low0
    if hl > 0:
        close_position = (close0 - low0) / hl
        body = abs(close0 - open0) / hl
        upper_shadow = (high0 - max(open0, close0)) / hl
        lower_shadow = (min(open0, close0) - low0) / hl
    else:
        close_position = 0.5
        body = 0.0
        upper_shadow = 0.0
        lower_shadow = 0.0

    # Gap
    gap_pct = (open0 / prev_close - 1.0) if prev_close > 0 else 0.0
    intraday_ret = (close0 / open0 - 1.0) if open0 > 0 else 0.0

    # Short-term returns
    def ret_n(n_days):
        if len(closes) > n_days:
            base = closes[-(n_days + 1)]
            return (closes[-1] / base - 1.0) if base > 0 else 0.0
        return None

    # Avg turnover (close * volume, proxy in TWD thousands)
    avg_turnover20 = None
    if len(rows) >= 20:
        turnovers = [_sf(rows[i][4]) * _sf(rows[i][5]) / 1000 for i in range(n - 20, n)]
        avg_turnover20 = sum(turnovers) / 20

    return {
        "close": close0, "open": open0, "high": high0, "low": low0, "vol": vol0,
        "prev_close": prev_close,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "avg_vol20": avg_vol20, "volume_ratio": volume_ratio,
        "rsi14": rsi14,
        "close_position": close_position, "body": body,
        "upper_shadow": upper_shadow, "lower_shadow": lower_shadow,
        "gap_pct": gap_pct, "intraday_ret": intraday_ret,
        "ret5": ret_n(5), "ret10": ret_n(10), "ret20": ret_n(20), "ret60": ret_n(60),
        "avg_turnover20": avg_turnover20,
    }


# ════════════════════════════════════════════════
# VolumeScorer
# ════════════════════════════════════════════════

def compute_volume_score(tech: dict) -> float:
    """
    volume_score 0~100.
    放量上漲好，放量黑K差，縮量中性偏低。
    """
    if not tech:
        return 50.0

    ratio = tech["volume_ratio"]
    intraday = tech["intraday_ret"]
    cp = tech["close_position"]
    us = tech["upper_shadow"]

    score = 50.0  # 基礎

    # 放量 + 收紅 + close_position 好
    if ratio >= 2.0 and intraday > 0.01 and cp >= 0.6 and us < 0.35:
        score = min(95, 50 + ratio * 12)
    elif ratio >= 1.5 and intraday > 0:
        score = min(80, 50 + ratio * 8)
    # 放量 + 爆量長上影（可能出貨）
    elif ratio >= 2.0 and us >= 0.4:
        score = max(20, 50 - us * 50)
    # 放量收黑
    elif ratio >= 2.0 and intraday < -0.01:
        score = max(10, 50 - ratio * 10)
    # 縮量
    elif ratio < 0.5:
        score = max(20, 50 - (1 - ratio) * 30)
    elif ratio < 0.8:
        score = max(35, 50 - (1 - ratio) * 20)
    # 正常量
    else:
        score = 50.0 + intraday * 200  # 漲跌微調

    return round(float(max(0, min(100, score))), 2)


# ════════════════════════════════════════════════
# EntryScorer
# ════════════════════════════════════════════════

def compute_entry_score(tech: dict) -> float:
    """
    entry_score 0~100，回答「現在是不是好買點？」
    初始 60，按條件加減分。
    """
    if not tech:
        return 50.0

    score = 60.0
    close = tech["close"]
    ma20  = tech["ma20"]
    ma60  = tech["ma60"]
    rsi14 = tech["rsi14"]
    ratio = tech["volume_ratio"]
    intraday = tech["intraday_ret"]
    us    = tech["upper_shadow"]
    gap   = tech["gap_pct"]
    cp    = tech["close_position"]
    ret5  = tech.get("ret5") or 0.0

    # 加分：趨勢向上
    if ma20 and close > ma20:
        score += 5
    if ma20 and ma60 and ma20 >= ma60:
        score += 5

    # 加分：RSI 合理區間
    if rsi14 is not None:
        if 45 <= rsi14 <= 65:
            score += 8
        elif 35 <= rsi14 < 45:
            score += 5   # 超賣反彈
        elif 65 < rsi14 <= 75:
            score += 0   # 偏熱，不加分
        elif rsi14 > 75:
            score -= 10  # 過熱

    # 加分：放量上漲，close_position 好
    if ratio >= 1.5 and intraday > 0.01 and cp >= 0.6:
        score += 8
    elif ratio >= 1.0 and intraday > 0:
        score += 3

    # 扣分：離 MA20 太遠（追高風險）
    if ma20 and ma20 > 0:
        dist = (close - ma20) / ma20
        if dist > 0.20:
            score -= 15
        elif dist > 0.15:
            score -= 10
        elif dist > 0.10:
            score -= 5
        elif dist < -0.05:
            score -= 5   # 跌破 MA20

    # 扣分：開高走低
    if gap > 0.03 and intraday < -0.02:
        score -= 12

    # 扣分：長上影線
    if us >= 0.45:
        score -= 10
    elif us >= 0.35:
        score -= 5

    # 扣分：放量黑K
    if ratio >= 2.0 and intraday < -0.01:
        score -= 15

    # 扣分：短線已漲很多
    if ret5 > 0.15:
        score -= 12
    elif ret5 > 0.10:
        score -= 6

    return round(float(max(0, min(100, score))), 2)


# ════════════════════════════════════════════════
# RiskScorer
# ════════════════════════════════════════════════

RISK_FLAG_WEIGHTS = {
    "short_term_overheat":         10,
    "too_far_from_ma":             8,
    "rsi_overheated":              12,
    "consecutive_limit_up":        20,
    "high_volume_upper_shadow":    18,  # 重大
    "gap_up_fade":                 14,
    "high_volume_black_candle":    18,  # 重大
    "limit_up_opened":             16,  # 重大
    "hot_money_day_trade_risk":    14,
    "price_volume_divergence":     10,
    "institutions_sell_retail_buy": 16, # 重大
}

MAJOR_FLAGS = {
    "high_volume_upper_shadow",
    "high_volume_black_candle",
    "limit_up_opened",
    "institutions_sell_retail_buy",
}


def compute_risk_score_and_flags(tech: dict, code: str, score_date: date, db: Session,
                                  chip_net: float = None) -> tuple[float, list[str]]:
    """
    回傳 (risk_score 0~100, risk_flags list)
    risk_score 越高越危險。
    """
    if not tech:
        return 30.0, []

    flags = []
    close  = tech["close"]
    open0  = tech["open"]
    high0  = tech["high"]
    ma20   = tech["ma20"]
    ma60   = tech["ma60"]
    rsi14  = tech["rsi14"]
    ratio  = tech["volume_ratio"]
    us     = tech["upper_shadow"]
    gap    = tech["gap_pct"]
    intra  = tech["intraday_ret"]
    cp     = tech["close_position"]
    ret5   = tech.get("ret5") or 0.0
    ret10  = tech.get("ret10") or 0.0
    ret20  = tech.get("ret20") or 0.0
    prev_close = tech["prev_close"]

    # 1. short_term_overheat
    if ret5 > 0.15 or ret10 > 0.25 or ret20 > 0.40:
        flags.append("short_term_overheat")

    # 2. too_far_from_ma
    if ma20 and ma20 > 0 and (close - ma20) / ma20 > 0.15:
        flags.append("too_far_from_ma")
    if ma60 and ma60 > 0 and (close - ma60) / ma60 > 0.30:
        if "too_far_from_ma" not in flags:
            flags.append("too_far_from_ma")

    # 3. rsi_overheated
    if rsi14 is not None and rsi14 > 75:
        flags.append("rsi_overheated")

    # 4. consecutive_limit_up（近2日 change_pct >= 9.5%）
    try:
        limit_rows = db.execute(text("""
            SELECT change_pct FROM ohlcv_daily
            WHERE code=:code AND trade_date <= :d
              AND change_pct IS NOT NULL
            ORDER BY trade_date DESC LIMIT 2
        """), {"code": code, "d": score_date}).fetchall()
        if len(limit_rows) >= 2 and all(_sf(r[0]) >= 9.5 for r in limit_rows):
            flags.append("consecutive_limit_up")
    except Exception:
        pass

    # 5. high_volume_upper_shadow
    if ratio >= 2.0 and us >= 0.45 and cp <= 0.55:
        flags.append("high_volume_upper_shadow")

    # 6. gap_up_fade
    if gap > 0.03 and intra < -0.02 and ratio >= 1.5:
        flags.append("gap_up_fade")

    # 7. high_volume_black_candle
    if close < open0 and ratio >= 2.0 and cp <= 0.4:
        flags.append("high_volume_black_candle")

    # 8. limit_up_opened（漲停打開）
    if prev_close > 0:
        high_pct = (high0 / prev_close - 1) * 100
        close_pct = (close / prev_close - 1) * 100
        if high_pct >= 9.5 and close_pct < 8.0 and ratio >= 2.0:
            flags.append("limit_up_opened")

    # 9. hot_money_day_trade_risk
    is_core = code in CORE_POOL
    if not is_core and intra > 0.07 and ratio >= 2.5 and us >= 0.3:
        flags.append("hot_money_day_trade_risk")

    # 10. price_volume_divergence
    if close == tech.get("high"):  # 近20日新高
        try:
            max_close = db.execute(text("""
                SELECT MAX(close) FROM ohlcv_daily
                WHERE code=:code AND trade_date <= :d
                  AND trade_date > date(:d, '-20 days')
            """), {"code": code, "d": score_date}).scalar()
            if max_close and close >= max_close and ratio < 0.8:
                flags.append("price_volume_divergence")
        except Exception:
            pass

    # 11. institutions_sell_retail_buy
    if chip_net is not None and chip_net < -500 and ratio >= 2.0 and intra > 0.03:
        flags.append("institutions_sell_retail_buy")

    # 計算 risk_score
    base = 20.0  # 基礎風險
    for flag in flags:
        base += RISK_FLAG_WEIGHTS.get(flag, 5)

    risk_score = min(100, base)
    return round(risk_score, 2), flags


# ════════════════════════════════════════════════
# StockClassifier
# ════════════════════════════════════════════════

def classify_stock(code: str, tech: dict, risk_score: float, risk_flags: list[str]) -> str:
    """
    CORE_LARGE_CAP / LIQUID_MOMENTUM / SPECULATIVE_HOT / ILLIQUID_RISK / NORMAL
    """
    if not tech:
        return "NORMAL"

    avg_turnover = tech.get("avg_turnover20") or 0  # 千元
    ratio = tech["volume_ratio"]
    ret5  = tech.get("ret5") or 0.0
    close = tech["close"]
    ma20  = tech["ma20"]
    ma60  = tech["ma60"]

    # 核心大型股
    if code in CORE_POOL or avg_turnover >= 100_000:  # 1億元以上（千元單位）
        return "CORE_LARGE_CAP"

    # 低流動性高風險
    if avg_turnover < 5_000:  # 500萬元以下
        return "ILLIQUID_RISK"

    # 短線題材熱股
    is_hot = (
        ret5 > 0.15 or ratio >= 2.5 or
        "consecutive_limit_up" in risk_flags or
        "limit_up_opened" in risk_flags or
        risk_score >= 60
    )
    if is_hot:
        return "SPECULATIVE_HOT"

    # 流動性強勢股
    trend_ok = (
        ma20 and close > ma20 and
        ma60 and ma20 >= ma60 and
        avg_turnover >= 10_000  # 1000萬
    )
    if trend_ok and risk_score <= 55:
        return "LIQUID_MOMENTUM"

    return "NORMAL"


# ════════════════════════════════════════════════
# 複合分數計算
# ════════════════════════════════════════════════

def compute_candidate_score(scores: dict) -> float:
    """
    candidate_score = 候選股強度分
    fundamental*0.20 + valuation*0.15 + chip*0.20 + momentum*0.15 +
    volume*0.10 + macro*0.10 + news*0.10
    """
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
    """
    core_score = 核心股適合度
    fundamental*0.30 + valuation*0.20 + chip*0.15 + momentum*0.10 +
    volume*0.05 + macro*0.15 + news*0.05
    """
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
    """
    final_score = candidate*0.6 + entry*0.4 - risk_penalty
    risk_penalty = max(0, risk - 40) * 0.5
    """
    penalty = max(0.0, risk - 40) * 0.5
    score = candidate * 0.6 + entry * 0.4 - penalty
    return round(max(0, min(100, score)), 2)


def compute_final_action(candidate: float, entry: float, risk: float,
                          stock_class: str, risk_flags: list[str]) -> str:
    """
    BUY / WATCH / AVOID_CHASE / HOLD
    """
    has_major = bool(set(risk_flags) & MAJOR_FLAGS)

    if (candidate >= 60 and entry >= 55 and risk <= 45
            and stock_class != "ILLIQUID_RISK" and not has_major):
        return "BUY"

    if candidate >= 65 and (risk >= 60 or has_major):
        return "AVOID_CHASE"

    if candidate >= 65 and (entry < 55 or risk > 45):
        return "WATCH"

    return "HOLD"


# ════════════════════════════════════════════════
# 批次計算並寫入
# ════════════════════════════════════════════════

def compute_extended_scores(codes: list[str], score_date: date):
    """
    在 compute_scores 之後補充計算擴充分數，
    寫入 volume_score / candidate_score / entry_score /
         risk_score / risk_flags / final_score / final_action /
         core_score / stock_class
    """
    db = SessionLocal()
    updated = 0

    for code in codes:
        try:
            # 取既有分數
            row = db.execute(text("""
                SELECT fundamental_score, valuation_score, chip_score,
                       momentum_score, macro_score, news_score, composite_score
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

            # 技術資料
            tech = _get_tech_data(code, score_date, db)

            # 法人買賣超
            chip_row = db.execute(text("""
                SELECT foreign_net + COALESCE(trust_net,0) + COALESCE(dealer_net,0)
                FROM chip_daily WHERE code=:code
                AND trade_date = (SELECT MAX(trade_date) FROM chip_daily
                                   WHERE code=:code AND trade_date <= :d)
            """), {"code": code, "d": score_date}).scalar()
            chip_net = _sf(chip_row)

            # 各分數
            volume_score  = compute_volume_score(tech)
            entry_score   = compute_entry_score(tech)
            risk_score, risk_flags = compute_risk_score_and_flags(
                tech, code, score_date, db, chip_net)

            all_scores = {**base_scores, "volume_score": volume_score}
            candidate_score = compute_candidate_score(all_scores)
            core_score      = compute_core_score(all_scores)
            final_score     = compute_final_score(candidate_score, entry_score, risk_score)
            stock_class     = classify_stock(code, tech, risk_score, risk_flags)
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
                "vs": volume_score, "cs": candidate_score,
                "es": entry_score,  "rs": risk_score,
                "rf": json.dumps(risk_flags, ensure_ascii=False),
                "fs": final_score,  "fa": final_action,
                "cor": core_score,  "sc": stock_class,
                "code": code, "d": score_date,
            })
            updated += 1

        except Exception as e:
            logger.warning(f"[SCORER_V2] {code}: {e}")

    db.commit()
    db.close()
    logger.success(f"[SCORER_V2] {score_date} 擴充分數完成，共 {updated} 檔")
