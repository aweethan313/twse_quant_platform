"""
backend/v3/candidate_trade_plans.py
V3-FIX-11：候選股交易計畫
每個候選股都有進場區間、目標價、停損價、建議股數、風報比
"""
from __future__ import annotations
import json
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

try:
    from config.capital_config import CAPITAL_CONFIG as CFG
except ImportError:
    class _Cfg:
        default_stop_loss_pct = 0.08
        default_target_pct_1 = 0.10
        default_target_pct_2 = 0.15
        min_risk_reward_ratio = 1.5
        max_single_trade_loss_amount = 2000
        active_capital = 100_000
        min_cash_ratio = 0.10
        def calc_suggested_shares(self, ep, sp, rl="medium", lot_size=1):
            loss_ps = ep - sp
            if loss_ps <= 0: return {"shares":1,"amount":ep,"max_loss":loss_ps,"reason":""}
            shares = max(1, int(2000/loss_ps))
            return {"shares":shares,"amount":shares*ep,"max_loss":shares*loss_ps,"reason":""}
        def is_core_etf(self, code): return code == "0050"
    CFG = _Cfg()


def _get_ma20(code: str, plan_date: date, db) -> float | None:
    rows = db.execute(text("""
        SELECT close FROM ohlcv_daily
        WHERE code=:c AND trade_date<=:d AND close IS NOT NULL
        ORDER BY trade_date DESC LIMIT 20
    """), {"c": code, "d": plan_date}).fetchall()
    if len(rows) < 20: return None
    return sum(float(r[0]) for r in rows) / 20


def _get_rsi14(code: str, plan_date: date, db) -> float | None:
    rows = db.execute(text("""
        SELECT close FROM ohlcv_daily
        WHERE code=:c AND trade_date<=:d AND close IS NOT NULL
        ORDER BY trade_date DESC LIMIT 15
    """), {"c": code, "d": plan_date}).fetchall()
    if len(rows) < 12: return None
    closes = [float(r[0]) for r in reversed(rows)]
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, 15)]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, 15)]
    ag, al = sum(gains)/14, sum(losses)/14
    return 100.0 if al == 0 else 100 - 100/(1+ag/al)


def generate_trade_plan(
    code: str,
    name: str,
    reference_price: float,
    plan_date: date,
    final_score: float = 50,
    risk_score: float = 30,
    risk_level: str = "medium",
    candidate_pool_type: str = "LIQUID_MOMENTUM",
    db=None,
) -> dict:
    """
    為單一候選股生成交易計畫
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        p = reference_price
        sl_pct  = CFG.default_stop_loss_pct
        t1_pct  = CFG.default_target_pct_1
        t2_pct  = CFG.default_target_pct_2

        # 根據 risk_score 調整參數
        if risk_score >= 60:
            sl_pct = 0.06    # 風險高，收緊停損
            t1_pct = 0.08
        elif risk_score <= 25:
            t2_pct = 0.18    # 風險低，目標可以拉高

        stop_loss   = round(p * (1 - sl_pct), 2)
        target_1    = round(p * (1 + t1_pct), 2)
        target_2    = round(p * (1 + t2_pct), 2)
        downside    = sl_pct * 100
        return_1    = t1_pct * 100
        return_2    = t2_pct * 100
        rrr         = round(t1_pct / sl_pct, 2)

        # 技術面調整
        ma20 = _get_ma20(code, plan_date, db)
        rsi  = _get_rsi14(code, plan_date, db)

        position_notes = []
        size_multiplier = 1.0

        if ma20 and p > 0:
            dist = (p - ma20) / ma20
            if dist > 0.15:
                size_multiplier *= 0.5
                position_notes.append(f"距 MA20 {dist*100:.1f}%，縮小部位至50%")
            elif dist > 0.10:
                size_multiplier *= 0.7
                position_notes.append(f"距 MA20 {dist*100:.1f}%，縮小部位至70%")

        if rsi and rsi > 75:
            size_multiplier *= 0.5
            position_notes.append(f"RSI={rsi:.0f} 過熱，縮小部位")
        elif rsi and rsi > 65:
            size_multiplier *= 0.75
            position_notes.append(f"RSI={rsi:.0f} 偏熱，略縮部位")

        if risk_level == "high":
            size_multiplier *= 0.6
            position_notes.append("高風險市場，縮小部位")

        # 進場區間
        entry_low  = round(p * 0.99, 2)   # 略低於收盤
        entry_high = round(p * 1.02, 2)   # 開盤2%內可追

        # 計算建議股數
        sz = CFG.calc_suggested_shares(entry_low, stop_loss, risk_level)
        shares = max(1, int(sz["shares"] * size_multiplier))
        amount = round(shares * entry_low, 0)
        max_loss = round(shares * (entry_low - stop_loss), 0)

        # 不建議追高判斷
        invalid_conditions = []
        if rrr < 1.2:  # 10%目標/8%停損=1.25，用1.2門檻
            invalid_conditions.append(f"風報比 {rrr} < {CFG.min_risk_reward_ratio}，不建議")
        if ma20 and (p - ma20)/ma20 > 0.15:
            invalid_conditions.append("距均線過遠，不可追高")
        if rsi and rsi > 78:
            invalid_conditions.append(f"RSI={rsi:.0f} 嚴重過熱，等回檔")
        if risk_score >= 65:
            invalid_conditions.append(f"風險分 {risk_score:.0f} 過高")

        summary_parts = [f"進場 {entry_low}～{entry_high}"]
        summary_parts.append(f"目標 {target_1}（+{return_1:.0f}%）/ {target_2}（+{return_2:.0f}%）")
        summary_parts.append(f"停損 {stop_loss}（-{downside:.0f}%）")
        summary_parts.append(f"建議 {shares} 股 / {amount:,.0f} 元 / 最大虧損 {max_loss:,.0f} 元")
        if invalid_conditions:
            summary_parts.append("⚠️ " + "；".join(invalid_conditions))

        plan = {
            "plan_date": str(plan_date),
            "code": code,
            "name": name,
            "candidate_pool_type": candidate_pool_type,
            "reference_price": p,
            "entry_price_low": entry_low,
            "entry_price_high": entry_high,
            "target_price_1": target_1,
            "target_price_2": target_2,
            "stop_loss_price": stop_loss,
            "expected_return_1": round(return_1, 1),
            "expected_return_2": round(return_2, 1),
            "downside_risk": round(downside, 1),
            "risk_reward_ratio": rrr,
            "suggested_shares": shares,
            "suggested_amount": amount,
            "max_loss_amount": max_loss,
            "position_size_reason": "；".join(position_notes) if position_notes else "標準部位",
            "invalid_buy_condition": "；".join(invalid_conditions) if invalid_conditions else None,
            "final_plan_summary": "，".join(summary_parts),
            "ma20": round(ma20, 2) if ma20 else None,
            "rsi14": round(rsi, 1) if rsi else None,
            "size_multiplier": round(size_multiplier, 2),
        }

        # 寫入 DB
        db.execute(text("""
            INSERT INTO candidate_trade_plans (
                plan_date, code, name, candidate_pool_type,
                entry_price_low, entry_price_high, reference_price,
                target_price_1, target_price_2, stop_loss_price,
                expected_return_1, expected_return_2, downside_risk,
                risk_reward_ratio, suggested_shares, suggested_amount,
                max_loss_amount, position_size_reason,
                invalid_buy_condition, final_plan_summary
            ) VALUES (
                :pd,:code,:name,:cpt,
                :el,:eh,:rp,
                :t1,:t2,:sl,
                :er1,:er2,:dr,
                :rrr,:ss,:sa,
                :ml,:psr,
                :ibc,:fps
            )
        """), {
            "pd": str(plan_date), "code": code, "name": name, "cpt": candidate_pool_type,
            "el": entry_low, "eh": entry_high, "rp": p,
            "t1": target_1, "t2": target_2, "sl": stop_loss,
            "er1": return_1, "er2": return_2, "dr": downside,
            "rrr": rrr, "ss": shares, "sa": amount,
            "ml": max_loss, "psr": plan["position_size_reason"],
            "ibc": plan["invalid_buy_condition"], "fps": plan["final_plan_summary"],
        })
        db.commit()
        return plan

    except Exception as e:
        logger.warning(f"[TRADE_PLAN] {code}: {e}")
        db.rollback()
        return {}
    finally:
        if close_db:
            db.close()


def generate_daily_plans(plan_date: date = None, limit: int = 30) -> list[dict]:
    """為今日候選清單的所有股票生成交易計畫"""
    if plan_date is None:
        plan_date = date.today()

    db = SessionLocal()
    try:
        # 取今日候選股
        rows = db.execute(text("""
            SELECT ds.code, sm.name, o.close,
                   ds.final_score, ds.risk_score, ds.stock_class,
                   ds.final_action
            FROM daily_scores ds
            LEFT JOIN stock_meta sm ON sm.code=ds.code
            LEFT JOIN ohlcv_daily o ON o.code=ds.code
              AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
            WHERE ds.score_date=(SELECT MAX(score_date) FROM daily_scores)
              AND ds.final_action IN ('BUY','WATCH')
              AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','SPECULATIVE_HOT','NORMAL')
              AND o.close IS NOT NULL
              AND o.close >= 10
              AND (
                  SELECT rsi14 FROM technical_daily_features
                  WHERE code=ds.code AND trade_date=ds.score_date LIMIT 1
              ) < 80
              AND (
                  SELECT rsi14 FROM technical_daily_features
                  WHERE code=ds.code AND trade_date=ds.score_date LIMIT 1
              ) > 30
              AND (
                  SELECT ABS(distance_ma20) FROM technical_daily_features
                  WHERE code=ds.code AND trade_date=ds.score_date LIMIT 1
              ) < 12
              AND (
                  SELECT return_5d FROM technical_daily_features
                  WHERE code=ds.code AND trade_date=ds.score_date LIMIT 1
              ) < 12
            ORDER BY
                CASE ds.stock_class
                    WHEN 'CORE_LARGE_CAP' THEN 1
                    WHEN 'LARGE_LIQUID' THEN 2
                    WHEN 'LIQUID_MOMENTUM' THEN 3
                    ELSE 4
                END,
                ds.final_score DESC
            LIMIT :n
        """), {"n": min(limit, 15)}).fetchall()

        # 取 risk_level
        ctx = db.execute(text("""
            SELECT trend_regime, breadth_score FROM market_context_daily
            ORDER BY context_date DESC LIMIT 1
        """)).fetchone()
        risk_level = "medium"
        if ctx:
            bs = float(ctx[1] or 50)
            risk_level = "low" if bs >= 60 else "high" if bs <= 35 else "medium"

        plans = []
        for r in rows:
            code, name, close, fs, rs, sc, fa = r
            if not close: continue
            plan = generate_trade_plan(
                code=code, name=name or code,
                reference_price=float(close),
                plan_date=plan_date,
                final_score=float(fs or 50),
                risk_score=float(rs or 30),
                risk_level=risk_level,
                candidate_pool_type=sc or "NORMAL",
                db=db,
            )
            if plan:
                plan["final_action"] = fa
                plans.append(plan)

        logger.success(f"[TRADE_PLAN] {plan_date} 生成 {len(plans)} 個交易計畫")
        return plans
    finally:
        db.close()


def get_trade_plans(
    plan_date: str = None,
    code: str = None,
    min_score: float = None,
    limit: int = 50,
) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM candidate_trade_plans WHERE 1=1"
        params = {}
        if plan_date: q += " AND plan_date=:pd"; params["pd"] = plan_date
        if code:      q += " AND code=:code";    params["code"] = code
        q += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit

        rows = db.execute(text(q), params).fetchall()
        cols = ["id","plan_date","code","name","candidate_pool_type",
                "entry_price_low","entry_price_high","reference_price",
                "target_price_1","target_price_2","stop_loss_price",
                "expected_return_1","expected_return_2","downside_risk",
                "risk_reward_ratio","suggested_shares","suggested_amount",
                "max_loss_amount","position_size_reason",
                "invalid_buy_condition","final_plan_summary","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
