"""
backend/v3/decision_explanations.py
V3-FIX-1：決策理由紀錄
每一筆 BUY / SELL / HOLD 都有理由
"""
from __future__ import annotations
import json
from datetime import datetime, date
from typing import Optional
from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend.models.database import SessionLocal


def _sf(x, d=0.0):
    try: return float(x or d)
    except: return d


def _generate_technical_reason(scores: dict) -> str:
    m = _sf(scores.get("momentum_score", 50))
    v = _sf(scores.get("volume_score", 50))
    reasons = []
    if m >= 65: reasons.append(f"動能強（{m:.0f}）")
    elif m <= 35: reasons.append(f"動能弱（{m:.0f}）")
    if v >= 65: reasons.append(f"量能放大（{v:.0f}）")
    elif v <= 35: reasons.append(f"量能萎縮（{v:.0f}）")
    return "；".join(reasons) if reasons else f"動能{m:.0f} 量能{v:.0f}，表現平穩"


def _generate_fundamental_reason(scores: dict) -> str:
    f = _sf(scores.get("fundamental_score", 50))
    val = _sf(scores.get("valuation_score", 50))
    if f >= 65 and val >= 50: return f"基本面良好（{f:.0f}），估值合理（{val:.0f}）"
    if f >= 65 and val < 40: return f"基本面良好（{f:.0f}），但估值偏貴（{val:.0f}）"
    if f < 40: return f"基本面偏弱（{f:.0f}），需謹慎"
    return f"基本面{f:.0f} 估值{val:.0f}"


def _generate_chip_reason(scores: dict) -> str:
    c = _sf(scores.get("chip_score", 50))
    if c >= 65: return f"法人買超明顯（{c:.0f}），籌碼面佳"
    if c <= 35: return f"法人賣超（{c:.0f}），籌碼轉弱"
    return f"籌碼中性（{c:.0f}）"


def _generate_final_explanation(action: str, scores: dict,
                                 blocked_reason: Optional[str]) -> str:
    fs = _sf(scores.get("final_score") or scores.get("composite_score", 50))
    risk = _sf(scores.get("risk_score", 30))

    if blocked_reason:
        return f"最終分 {fs:.1f}，但被擋下：{blocked_reason}"

    if action == "BUY":
        return f"最終分 {fs:.1f}，風險分 {risk:.0f}，符合買入條件"
    elif action == "SELL":
        return f"最終分 {fs:.1f}，風險過高或訊號反轉，執行賣出"
    elif action == "AVOID_CHASE":
        return f"候選分高但風險分 {risk:.0f} 過高，不可追高"
    elif action == "WATCH":
        return f"最終分 {fs:.1f}，條件接近但進場時機未到，持續觀察"
    else:
        return f"最終分 {fs:.1f}，未達買入標準，維持觀察"


def record_decision(
    trade_date: date,
    code: str,
    action: str,
    scores: dict,
    account_id: int = None,
    strategy_id: int = None,
    name: str = None,
    blocked_reason: str = None,
    risk_reason: str = None,
    market_regime_reason: str = None,
    db: Session = None,
):
    """
    記錄一筆決策（BUY / SELL / HOLD / WATCH / AVOID_CHASE）
    可傳入已有的 db session，或自行開啟
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_explanation = _generate_final_explanation(action, scores, blocked_reason)

        db.execute(text("""
            INSERT INTO decision_explanations (
                decision_time, trade_date, account_id, strategy_id,
                code, name, action,
                final_score, technical_score, volume_score,
                fundamental_score, valuation_score, chip_score,
                news_theme_score, market_regime_score,
                technical_reason, volume_reason, fundamental_reason,
                valuation_reason, chip_reason, news_theme_reason,
                market_regime_reason, risk_reason, blocked_reason,
                final_explanation
            ) VALUES (
                :dt, :td, :aid, :sid,
                :code, :name, :action,
                :fs, :ts, :vs,
                :fds, :vals, :cs,
                :ns, :ms,
                :tr, :vr, :fr,
                :valr, :cr, :nr,
                :mr, :rr, :br,
                :fe
            )
        """), {
            "dt": now, "td": str(trade_date), "aid": account_id, "sid": strategy_id,
            "code": code, "name": name or code, "action": action,
            "fs": scores.get("final_score") or scores.get("composite_score"),
            "ts": scores.get("momentum_score"),
            "vs": scores.get("volume_score"),
            "fds": scores.get("fundamental_score"),
            "vals": scores.get("valuation_score"),
            "cs": scores.get("chip_score"),
            "ns": scores.get("news_score"),
            "ms": scores.get("macro_score"),
            "tr": _generate_technical_reason(scores),
            "vr": f"量能分 {_sf(scores.get('volume_score',50)):.0f}",
            "fr": _generate_fundamental_reason(scores),
            "valr": f"估值分 {_sf(scores.get('valuation_score',50)):.0f}",
            "cr": _generate_chip_reason(scores),
            "nr": f"新聞分 {_sf(scores.get('news_score',50)):.0f}",
            "mr": market_regime_reason or "大盤環境正常",
            "rr": risk_reason or f"風險分 {_sf(scores.get('risk_score',30)):.0f}",
            "br": blocked_reason,
            "fe": final_explanation,
        })
        db.commit()
    except Exception as e:
        logger.warning(f"[DECISION] 記錄失敗 {code}: {e}")
        db.rollback()
    finally:
        if close_db:
            db.close()


def record_hold(trade_date: date, code: str, scores: dict,
                reason: str = None, account_id: int = None,
                strategy_id: int = None, name: str = None):
    """記錄 HOLD 理由"""
    record_decision(
        trade_date=trade_date, code=code, action="HOLD",
        scores=scores, account_id=account_id,
        strategy_id=strategy_id, name=name,
        blocked_reason=reason,
    )


def record_batch(decisions: list[dict]):
    """批次寫入多筆決策"""
    db = SessionLocal()
    for d in decisions:
        record_decision(db=db, **d)
    db.close()


def query_explanations(
    trade_date: str = None,
    account_id: int = None,
    strategy_id: int = None,
    code: str = None,
    action: str = None,
    limit: int = 100,
) -> list[dict]:
    """查詢決策理由"""
    db = SessionLocal()
    try:
        q = "SELECT * FROM decision_explanations WHERE 1=1"
        params = {}
        if trade_date: q += " AND trade_date=:td"; params["td"] = trade_date
        if account_id: q += " AND account_id=:aid"; params["aid"] = account_id
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        if code: q += " AND code=:code"; params["code"] = code
        if action: q += " AND action=:action"; params["action"] = action
        q += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit

        rows = db.execute(text(q), params).fetchall()
        cols = ["id","decision_time","trade_date","account_id","strategy_id",
                "code","name","action","final_score","technical_score","volume_score",
                "fundamental_score","valuation_score","chip_score","news_theme_score",
                "market_regime_score","technical_reason","volume_reason","fundamental_reason",
                "valuation_reason","chip_reason","news_theme_reason","market_regime_reason",
                "risk_reason","blocked_reason","final_explanation","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
