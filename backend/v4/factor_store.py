"""
backend/v4/factor_store.py
V4-2：因子資料庫（從 daily_scores 抽取因子，記錄 available_at）
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


FACTOR_GROUPS = {
    "technical":   ["technical_score","entry_score","momentum_score","ma5","ma20","rsi14","ma60"],
    "volume":      ["volume_score","volume_ratio","turnover_rate"],
    "chip":        ["chip_score","foreign_net","trust_net","dealer_net"],
    "fundamental": ["fundamental_score","eps","roe","gross_margin","revenue_growth"],
    "valuation":   ["valuation_score","pe_ratio","pb_ratio","pe_percentile","pb_percentile"],
    "theme":       ["ai_theme_score","semiconductor_theme_score","pcb_theme_score","candidate_score"],
    "risk":        ["risk_score","volatility","beta"],
    "composite":   ["final_score","final_action","stock_class"],
}

# 對應 daily_scores 欄位
SCORE_COL_MAP = {
    "technical_score":       "technical_score",
    "entry_score":           "entry_score",
    "momentum_score":        "momentum_score",
    "chip_score":            "chip_score",
    "fundamental_score":     "fundamental_score",
    "valuation_score":       "valuation_score",
    "candidate_score":       "candidate_score",
    "risk_score":            "risk_score",
    "final_score":           "final_score",
    "volume_score":          "volume_score",
    "ai_theme_score":        "ai_theme_score",
}

OHLCV_COL_MAP = {
    "volume_ratio":  "volume / (SELECT AVG(v2.volume) FROM ohlcv_daily v2 WHERE v2.code=o.code AND v2.trade_date < o.trade_date AND v2.trade_date >= date(o.trade_date, '-20 days') AND v2.volume > 0)",
}


def build_factor_store(target_date: date = None, codes: list[str] = None) -> int:
    """
    從 daily_scores + ohlcv_daily 抽取因子寫入 factor_store
    available_at 設為收盤後 18:00（日K因子當天收盤後才可用）
    """
    if target_date is None:
        target_date = date.today()

    available_at = f"{target_date} 18:00:00"
    db = SessionLocal()
    inserted = 0

    try:
        # 取 daily_scores
        q = """
            SELECT ds.code, sm.name,
                   ds.technical_score, ds.entry_score, ds.momentum_score,
                   ds.chip_score, ds.fundamental_score, ds.valuation_score,
                   ds.candidate_score, ds.risk_score, ds.final_score,
                   ds.volume_score, ds.ai_theme_score, ds.stock_class,
                   ds.final_action
            FROM daily_scores ds
            LEFT JOIN stock_meta sm ON sm.code=ds.code
            WHERE ds.score_date=:d
        """
        params = {"d": str(target_date)}
        if codes:
            q += " AND ds.code IN (" + ",".join(f"'{c}'" for c in codes) + ")"

        rows = db.execute(text(q), params).fetchall()
        cols = ["code","name","technical_score","entry_score","momentum_score",
                "chip_score","fundamental_score","valuation_score",
                "candidate_score","risk_score","final_score",
                "volume_score","ai_theme_score","stock_class","final_action"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for row in rows:
            d = dict(zip(cols, row))
            code = d["code"]
            name = d["name"] or code

            # 寫入各因子
            factors = [
                ("technical_score",   d["technical_score"],  "technical",   "daily_scores"),
                ("entry_score",       d["entry_score"],       "technical",   "daily_scores"),
                ("momentum_score",    d["momentum_score"],    "technical",   "daily_scores"),
                ("chip_score",        d["chip_score"],        "chip",        "daily_scores"),
                ("fundamental_score", d["fundamental_score"], "fundamental", "daily_scores"),
                ("valuation_score",   d["valuation_score"],   "valuation",   "daily_scores"),
                ("candidate_score",   d["candidate_score"],   "composite",   "daily_scores"),
                ("risk_score",        d["risk_score"],        "risk",        "daily_scores"),
                ("final_score",       d["final_score"],       "composite",   "daily_scores"),
                ("volume_score",      d["volume_score"],      "volume",      "daily_scores"),
                ("ai_theme_score",    d["ai_theme_score"],    "theme",       "daily_scores"),
            ]

            for fname, fval, fgroup, src in factors:
                if fval is None:
                    continue
                confidence = 90 if fval is not None else 50
                db.execute(text("""
                    INSERT INTO factor_store
                        (factor_date, available_at, code, name, factor_name,
                         factor_value, factor_group, source_table, source_time,
                         confidence_score, updated_at)
                    VALUES (:fd,:aa,:code,:name,:fname,
                            :fval,:fg,:src,:st,:conf,:now)
                    ON CONFLICT(factor_date, code, factor_name) DO UPDATE SET
                        factor_value=excluded.factor_value,
                        available_at=excluded.available_at,
                        confidence_score=excluded.confidence_score,
                        updated_at=excluded.updated_at
                """), {
                    "fd": str(target_date), "aa": available_at,
                    "code": code, "name": name,
                    "fname": fname, "fval": float(fval),
                    "fg": fgroup, "src": src,
                    "st": str(target_date) + " 15:00:00",
                    "conf": confidence, "now": now_str,
                })
                inserted += 1

        # 從 ohlcv_daily 補充技術指標
        ohlcv_rows = db.execute(text("""
            SELECT code, close, open, high, low, volume,
                   change_pct, turnover_rate
            FROM ohlcv_daily WHERE trade_date=:d
        """), {"d": str(target_date)}).fetchall()

        for row in ohlcv_rows:
            code, close, open_, high, low, vol, chg_pct, tr = row
            if not close: continue

            # MA20 距離
            ma20_row = db.execute(text("""
                SELECT AVG(close) FROM (
                    SELECT close FROM ohlcv_daily
                    WHERE code=:c AND trade_date<=:d AND close IS NOT NULL
                    ORDER BY trade_date DESC LIMIT 20
                )
            """), {"c": code, "d": str(target_date)}).scalar()

            if ma20_row:
                ma20_dist = (float(close) - float(ma20_row)) / float(ma20_row) * 100
                db.execute(text("""
                    INSERT INTO factor_store
                        (factor_date, available_at, code, factor_name,
                         factor_value, factor_group, source_table, confidence_score, updated_at)
                    VALUES (:fd,:aa,:code,'ma20_distance',
                            :fval,'technical','ohlcv_daily',85,:now)
                    ON CONFLICT(factor_date, code, factor_name) DO UPDATE SET
                        factor_value=excluded.factor_value, updated_at=excluded.updated_at
                """), {"fd": str(target_date), "aa": available_at,
                       "code": code, "fval": round(ma20_dist, 2), "now": now_str})
                inserted += 1

            if chg_pct is not None:
                db.execute(text("""
                    INSERT INTO factor_store
                        (factor_date, available_at, code, factor_name,
                         factor_value, factor_group, source_table, confidence_score, updated_at)
                    VALUES (:fd,:aa,:code,'change_pct_1d',
                            :fval,'technical','ohlcv_daily',95,:now)
                    ON CONFLICT(factor_date, code, factor_name) DO UPDATE SET
                        factor_value=excluded.factor_value, updated_at=excluded.updated_at
                """), {"fd": str(target_date), "aa": available_at,
                       "code": code, "fval": float(chg_pct), "now": now_str})
                inserted += 1

        db.commit()
        logger.success(f"[FACTOR] {target_date} 寫入 {inserted} 個因子")
        return inserted

    except Exception as e:
        logger.error(f"[FACTOR] 失敗: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def get_factors(code: str = None, factor_date: str = None,
                factor_group: str = None, factor_name: str = None,
                decision_time: str = None, limit: int = 200) -> list[dict]:
    """
    查詢因子（可過濾 available_at <= decision_time，確保不偷看未來）
    """
    db = SessionLocal()
    try:
        q = "SELECT * FROM factor_store WHERE 1=1"
        params = {}
        if code:         q += " AND code=:code";         params["code"] = code
        if factor_date:  q += " AND factor_date=:fd";    params["fd"] = factor_date
        if factor_group: q += " AND factor_group=:fg";   params["fg"] = factor_group
        if factor_name:  q += " AND factor_name=:fn";    params["fn"] = factor_name
        if decision_time:
            q += " AND available_at<=:dt"
            params["dt"] = decision_time
        q += " ORDER BY factor_date DESC, code LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","factor_date","available_at","code","name","factor_name",
                "factor_value","factor_group","source_table","source_time",
                "confidence_score","created_at","updated_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


def check_no_lookahead(decision_time: str, code: str = None) -> list[str]:
    """驗證沒有使用 available_at > decision_time 的因子"""
    db = SessionLocal()
    try:
        q = """
            SELECT code, factor_name, available_at, factor_date
            FROM factor_store
            WHERE available_at > :dt
        """
        params = {"dt": decision_time}
        if code:
            q += " AND code=:code"
            params["code"] = code
        rows = db.execute(text(q), params).fetchall()
        return [f"{r[0]} {r[1]} available_at={r[2]} > decision_time={decision_time}"
                for r in rows[:10]]
    finally:
        db.close()
