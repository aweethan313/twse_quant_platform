"""
backend/v4/research.py
V4-5: Backtest vs Paper Gap
V4-6: Strategy Attribution
V4-7: Portfolio Optimizer
V4-10: Scenario Stress Test
"""
from __future__ import annotations
import json
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


# ═══════════════════════════════════
# V4-5: Backtest vs Paper Gap
# ═══════════════════════════════════

def analyze_backtest_paper_gap(strategy_id: int = None, analysis_date: date = None) -> list[dict]:
    if analysis_date is None:
        analysis_date = date.today()
    db = SessionLocal()
    results = []
    try:
        q = """
            SELECT rtf.code, rtf.signal_time, rtf.fill_time,
                   rtf.fill_price, rtf.reference_price,
                   rtf.execution_status, rtf.execution_reason,
                   rtf.strategy_id, rtf.slippage
            FROM realistic_trade_fills rtf
            WHERE rtf.execution_status IN ('FILLED','PARTIAL_FILLED','BLOCKED_BY_CORE_ETF_RULE')
              AND date(rtf.signal_time) >= date(:d, '-30 days')
        """
        params = {"d": str(analysis_date)}
        if strategy_id:
            q += " AND rtf.strategy_id=:sid"
            params["sid"] = strategy_id
        rows = db.execute(text(q), params).fetchall()

        for row in rows:
            code, sig_t, fill_t, fill_p, ref_p, status, reason, sid, slip = row
            if not ref_p or not fill_p: continue

            ref_p = float(ref_p); fill_p = float(fill_p)
            fill_gap = fill_p - ref_p
            fill_gap_pct = fill_gap / ref_p * 100

            # 取後續表現
            sig_date = str(sig_t)[:10] if sig_t else str(analysis_date)
            actual_rets = {}
            for n in [1, 3, 5]:
                r = db.execute(text(f"""
                    SELECT close FROM ohlcv_daily
                    WHERE code=:c AND trade_date > :d
                    ORDER BY trade_date LIMIT {n}
                """), {"c": code, "d": sig_date}).fetchall()
                if r:
                    actual_rets[f"actual_{n}d"] = round((float(r[-1][0]) / ref_p - 1) * 100, 2)

            # 判斷 gap_reason
            if status == "BLOCKED_BY_CORE_ETF_RULE":
                gap_reason = "風控擋掉但後來上漲" if actual_rets.get("actual_5d", 0) > 5 else "0050 核心保護"
            elif abs(fill_gap_pct) > 2:
                gap_reason = "回測成交價太樂觀"
            elif slip and float(slip) > ref_p * 0.003:
                gap_reason = "零股滑價高於假設"
            else:
                gap_reason = "正常成交"

            severity = "HIGH" if abs(fill_gap_pct) > 3 else "MEDIUM" if abs(fill_gap_pct) > 1 else "LOW"

            rec = {
                "analysis_date": str(analysis_date),
                "strategy_id": sid,
                "code": code,
                "signal_date": sig_date,
                "expected_fill_price": ref_p,
                "actual_fill_price": fill_p,
                "fill_price_gap": round(fill_gap, 2),
                "paper_actual_return_1d": actual_rets.get("actual_1d"),
                "paper_actual_return_3d": actual_rets.get("actual_3d"),
                "paper_actual_return_5d": actual_rets.get("actual_5d"),
                "missed_trade": 1 if status != "FILLED" else 0,
                "risk_blocked": 1 if "BLOCKED" in status else 0,
                "gap_reason": gap_reason,
                "severity": severity,
            }

            db.execute(text("""
                INSERT INTO backtest_paper_gap_analysis
                    (analysis_date, strategy_id, code, signal_date,
                     expected_fill_price, actual_fill_price, fill_price_gap,
                     paper_actual_return_1d, paper_actual_return_3d, paper_actual_return_5d,
                     missed_trade, risk_blocked, gap_reason, severity)
                VALUES (:ad,:sid,:code,:sigd,:efp,:afp,:fpg,
                        :r1,:r3,:r5,:mt,:rb,:gr,:sev)
            """), {
                "ad": str(analysis_date), "sid": sid, "code": code,
                "sigd": sig_date, "efp": ref_p, "afp": fill_p,
                "fpg": round(fill_gap, 2),
                "r1": rec["paper_actual_return_1d"],
                "r3": rec["paper_actual_return_3d"],
                "r5": rec["paper_actual_return_5d"],
                "mt": rec["missed_trade"], "rb": rec["risk_blocked"],
                "gr": gap_reason, "sev": severity,
            })
            results.append(rec)

        db.commit()
        logger.info(f"[GAP] {analysis_date} 分析 {len(results)} 筆")
        return results
    except Exception as e:
        logger.error(f"[GAP] 失敗: {e}")
        db.rollback()
        return []
    finally:
        db.close()


def get_gap_analysis(strategy_id: int = None, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM backtest_paper_gap_analysis WHERE 1=1"
        params = {}
        if strategy_id: q += " AND strategy_id=:sid"; params["sid"] = strategy_id
        q += " ORDER BY id DESC LIMIT :limit"; params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","analysis_date","strategy_id","account_id","code","signal_date",
                "backtest_expected_return_1d","backtest_expected_return_3d","backtest_expected_return_5d",
                "paper_actual_return_1d","paper_actual_return_3d","paper_actual_return_5d",
                "expected_fill_price","actual_fill_price","fill_price_gap","slippage_gap",
                "missed_trade","risk_blocked","gap_reason","severity","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


# ═══════════════════════════════════
# V4-6: Strategy Attribution
# ═══════════════════════════════════

def run_strategy_attribution(strategy_id: int = None, analysis_date: date = None) -> list[dict]:
    if analysis_date is None:
        analysis_date = date.today()
    db = SessionLocal()
    results = []
    try:
        strategies = db.execute(text(
            "SELECT id, name FROM strategy_accounts" +
            (" WHERE id=:sid" if strategy_id else "") +
            " ORDER BY id"
        ), {"sid": strategy_id} if strategy_id else {}).fetchall()

        for sid, sname in strategies:
            trades = db.execute(text("""
                SELECT t.code, t.direction, t.lots, t.price, t.pnl,
                       COALESCE(msc.primary_category, '其他') as category,
                       COALESCE(msc.theme_tags_json, '[]') as tags
                FROM trade_logs t
                LEFT JOIN market_sector_classification msc ON msc.code=t.code
                WHERE t.account_id=:sid AND t.pnl IS NOT NULL
                ORDER BY t.ts DESC LIMIT 200
            """), {"sid": sid}).fetchall()

            if not trades:
                continue

            # 按股票歸因
            by_stock = {}
            by_theme = {}
            total_pnl = sum(float(t[4] or 0) for t in trades)

            for code, direction, lots, price, pnl, cat, tags in trades:
                pnl = float(pnl or 0)
                by_stock[code] = by_stock.get(code, 0) + pnl
                by_theme[cat]  = by_theme.get(cat, 0) + pnl

            # 過度集中警告
            for code, pnl in by_stock.items():
                if total_pnl != 0 and abs(pnl / total_pnl) > 0.7:
                    conc_warn = 1
                else:
                    conc_warn = 0

                db.execute(text("""
                    INSERT INTO strategy_attribution
                        (analysis_date, strategy_id, account_id,
                         attribution_type, attribution_key,
                         total_pnl, pnl_contribution_pct, concentration_warning)
                    VALUES (:ad,:sid,:sid,'stock',:key,:pnl,:pct,:warn)
                """), {
                    "ad": str(analysis_date), "sid": sid,
                    "key": code, "pnl": round(pnl, 2),
                    "pct": round(pnl/total_pnl*100, 1) if total_pnl else 0,
                    "warn": conc_warn,
                })
                results.append({"strategy_id": sid, "type": "stock",
                                 "key": code, "pnl": pnl, "warn": conc_warn})

            for cat, pnl in by_theme.items():
                db.execute(text("""
                    INSERT INTO strategy_attribution
                        (analysis_date, strategy_id, account_id,
                         attribution_type, attribution_key,
                         total_pnl, pnl_contribution_pct, concentration_warning)
                    VALUES (:ad,:sid,:sid,'theme',:key,:pnl,:pct,0)
                """), {
                    "ad": str(analysis_date), "sid": sid,
                    "key": cat, "pnl": round(pnl, 2),
                    "pct": round(pnl/total_pnl*100, 1) if total_pnl else 0,
                })

        db.commit()
        logger.info(f"[ATTR] {analysis_date} 完成 {len(results)} 筆歸因")
        return results
    except Exception as e:
        logger.error(f"[ATTR] 失敗: {e}")
        db.rollback()
        return []
    finally:
        db.close()


# ═══════════════════════════════════
# V4-7: Portfolio Optimizer
# ═══════════════════════════════════

def run_portfolio_optimizer(account_id: int = None, plan_date: date = None) -> dict:
    if plan_date is None:
        plan_date = date.today()
    db = SessionLocal()
    try:
        # 取帳戶資產
        eq = db.execute(text("""
            SELECT total_equity, cash, market_value FROM equity_curve
            WHERE (:aid IS NULL OR account_id=:aid)
            ORDER BY snap_date DESC LIMIT 1
        """), {"aid": account_id}).fetchone()

        total = float(eq[0] or 200000) if eq else 200000
        cash  = float(eq[1] or 0) if eq else 0
        mktv  = float(eq[2] or 0) if eq else 0

        # 主題曝險
        from backend.v4.market_sector import get_theme_exposure
        exposure = get_theme_exposure(account_id)
        theme_exp = exposure.get("by_category", {})

        # 大盤風險
        ctx = db.execute(text("""
            SELECT trend_regime, breadth_score FROM market_context_daily
            ORDER BY context_date DESC LIMIT 1
        """)).fetchone()
        risk_level = "medium"
        if ctx:
            bs = float(ctx[1] or 50)
            risk_level = "low" if bs >= 60 else "high" if bs <= 35 else "medium"

        # 建議
        suggestions = []
        core_etf_ratio = 0.50
        active_ratio   = 0.50
        cash_ratio = cash / total * 100 if total else 0
        active_value = mktv

        if cash_ratio < 10:
            suggestions.append("⚠️ 現金比例低於10%，建議保留更多現金")
        if risk_level == "high":
            suggestions.append("🔴 高風險市場，建議短線部位縮減至30%")
            active_ratio = 0.30

        # 題材過度集中
        for cat, info in theme_exp.items():
            if info.get("ratio", 0) > 50:
                suggestions.append(f"⚠️ {cat} 曝險{info['ratio']:.0f}%過高，不建議再加碼")

        if not suggestions:
            suggestions.append("✅ 投組配置正常")

        plan = {
            "plan_date": str(plan_date),
            "account_id": account_id,
            "total_capital": total,
            "cash": cash,
            "cash_ratio": round(cash_ratio, 1),
            "market_value": mktv,
            "target_core_etf_ratio": core_etf_ratio,
            "target_active_ratio": active_ratio,
            "risk_level": risk_level,
            "theme_exposure": theme_exp,
            "suggestions": suggestions,
            "note": "輔助建議，非自動下單",
        }

        db.execute(text("""
            INSERT INTO portfolio_optimization_plans
                (plan_date, account_id, total_capital, cash,
                 target_core_etf_ratio, target_active_trading_ratio,
                 current_theme_exposure_json, risk_level, reason)
            VALUES (:pd,:aid,:tc,:cash,:tcr,:tar,:theme,:rl,:reason)
        """), {
            "pd": str(plan_date), "aid": account_id,
            "tc": total, "cash": cash,
            "tcr": core_etf_ratio, "tar": active_ratio,
            "theme": json.dumps(theme_exp, ensure_ascii=False),
            "rl": risk_level,
            "reason": "；".join(suggestions),
        })
        db.commit()
        return plan
    except Exception as e:
        logger.error(f"[PORTFOLIO] 失敗: {e}")
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()


# ═══════════════════════════════════
# V4-10: Scenario Stress Test
# ═══════════════════════════════════

SCENARIOS = [
    {"name": "台股大盤-3%",     "impact": -0.03, "affected": ["AI/半導體","AI伺服器","PCB/載板"]},
    {"name": "台股大盤-5%",     "impact": -0.05, "affected": ["AI/半導體","AI伺服器","PCB/載板","金融"]},
    {"name": "NASDAQ-3%",       "impact": -0.025,"affected": ["AI/半導體","AI伺服器"]},
    {"name": "SOX-5%",          "impact": -0.04, "affected": ["AI/半導體","PCB/載板"]},
    {"name": "TSMC ADR-5%",     "impact": -0.045,"affected": ["AI/半導體"]},
    {"name": "AI題材退燒",       "impact": -0.08, "affected": ["AI/半導體","AI伺服器","電源/散熱","PCB/載板"]},
    {"name": "PCB/CCL題材退燒",  "impact": -0.06, "affected": ["PCB/載板"]},
    {"name": "半導體全面回檔",    "impact": -0.07, "affected": ["AI/半導體"]},
    {"name": "台幣急升",         "impact": -0.02, "affected": ["AI伺服器","航運"]},
    {"name": "高估值股修正",      "impact": -0.05, "affected": ["AI/半導體","AI伺服器","電源/散熱"]},
]


def run_scenario_stress_test(account_id: int = None, test_date: date = None) -> list[dict]:
    if test_date is None:
        test_date = date.today()
    db = SessionLocal()
    results = []
    try:
        # 取持倉
        holdings = db.execute(text("""
            SELECT t.code, SUM(CASE WHEN t.direction='buy' THEN t.lots ELSE -t.lots END) as net_lots,
                   o.close,
                   COALESCE(msc.primary_category,'其他') as cat
            FROM trade_logs t
            LEFT JOIN ohlcv_daily o ON o.code=t.code
              AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
            LEFT JOIN market_sector_classification msc ON msc.code=t.code
            WHERE (:aid IS NULL OR t.account_id=:aid)
            GROUP BY t.code
            HAVING net_lots > 0
        """), {"aid": account_id}).fetchall()

        if not holdings:
            # 無持倉，用模擬資產
            holdings_val = {"其他": 50000}
        else:
            holdings_val = {}
            for code, lots, close, cat in holdings:
                val = float(lots or 0) * float(close or 0)
                holdings_val[cat] = holdings_val.get(cat, 0) + val

        total_val = sum(holdings_val.values()) or 100000

        for sc in SCENARIOS:
            affected_val = sum(holdings_val.get(cat, 0) for cat in sc["affected"])
            unaffected_val = total_val - affected_val

            estimated_pnl = affected_val * sc["impact"]
            estimated_return = estimated_pnl / total_val * 100 if total_val else 0

            max_loss_cat = max(
                ((cat, holdings_val.get(cat, 0) * sc["impact"])
                 for cat in sc["affected"]),
                key=lambda x: x[1],
                default=("N/A", 0)
            )

            warning = ""
            if estimated_return < -5:
                warning = f"⚠️ {sc['name']} 可能造成 {estimated_return:.1f}% 損失"
            if estimated_return < -10:
                warning = f"🔴 {sc['name']} 嚴重風險：{estimated_return:.1f}%"

            db.execute(text("""
                INSERT INTO scenario_stress_results
                    (test_date, scenario_name, account_id,
                     estimated_pnl, estimated_return,
                     affected_positions_json, max_loss_position,
                     theme_exposure_impact_json, risk_warning)
                VALUES (:td,:sn,:aid,:pnl,:ret,:apos,:mlp,:timp,:warn)
            """), {
                "td": str(test_date), "sn": sc["name"], "aid": account_id,
                "pnl": round(estimated_pnl, 0),
                "ret": round(estimated_return, 2),
                "apos": json.dumps(sc["affected"], ensure_ascii=False),
                "mlp": max_loss_cat[0],
                "timp": json.dumps({cat: round(holdings_val.get(cat,0)*sc["impact"],0)
                                    for cat in sc["affected"]}, ensure_ascii=False),
                "warn": warning,
            })

            results.append({
                "scenario": sc["name"],
                "estimated_pnl": round(estimated_pnl, 0),
                "estimated_return": round(estimated_return, 2),
                "warning": warning,
            })

        db.commit()
        logger.info(f"[STRESS] {test_date} 完成 {len(results)} 個情境測試")
        return results
    except Exception as e:
        logger.error(f"[STRESS] 失敗: {e}")
        db.rollback()
        return []
    finally:
        db.close()


def get_stress_results(test_date: str = None) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM scenario_stress_results WHERE 1=1"
        params = {}
        if test_date:
            q += " AND test_date=:td"; params["td"] = test_date
        else:
            q += " AND test_date=(SELECT MAX(test_date) FROM scenario_stress_results)"
        q += " ORDER BY estimated_return"
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","test_date","scenario_name","account_id","estimated_pnl",
                "estimated_return","affected_positions_json","max_loss_position",
                "theme_exposure_impact_json","risk_warning","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
