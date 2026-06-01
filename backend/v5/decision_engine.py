"""backend/v5/decision_engine.py
每日策略決策引擎：根據 strategy_account_configs 產生每個帳戶的決策
"""
from __future__ import annotations
from datetime import date, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


COST_FEE_RATE = 0.001425 * 0.38     # 手續費折扣後
COST_TAX_RATE = 0.003                 # 證交稅（賣出）
COST_SLIPPAGE_BUY  = 0.002
COST_SLIPPAGE_SELL = 0.003


def generate_strategy_decisions(signal_date: date = None) -> dict:
    """
    為所有啟用的 V5 策略帳戶產生 T+1 決策
    signal_date = T，execution_date = T+1
    """
    if signal_date is None:
        signal_date = date.today()

    db = SessionLocal()
    decisions_written = 0

    try:
        # 找下一個交易日
        next_day = db.execute(text("""
            SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > :d
        """), {"d": str(signal_date)}).scalar()
        execution_date = str(next_day) if next_day else str(signal_date + timedelta(days=1))

        # 取所有 V5 帳戶設定（account_id >= 11）
        configs = db.execute(text("""
            SELECT c.*, a.name as account_name, a.initial_cash
            FROM strategy_account_configs c
            JOIN strategy_accounts a ON a.id=c.account_id
            WHERE c.is_active=1 AND c.account_id >= 11
            ORDER BY c.account_id
        """)).fetchall()

        if not configs:
            logger.warning(f"[V5] 無啟用的 V5 策略帳戶")
            return {"ok": False, "message": "無 V5 帳戶"}

        col_names = ["id","account_id","strategy_name","mode",
                     "candidate_rank_limit","min_score","max_positions",
                     "max_position_pct","stop_loss_pct","take_profit_pct",
                     "min_hold_days","max_hold_days","allow_core_stocks","allow_small_caps",
                     "theme_filter","large_cap_only","no_chase_enabled",
                     "max_rsi14","min_rsi14","max_distance_ma20_pct",
                     "target_0050_pct","target_satellite_pct",
                     "market_risk_position_multiplier","description","is_active",
                     "created_at","updated_at","account_name","initial_cash"]

        for cfg_row in configs:
            cfg = dict(zip(col_names, cfg_row))
            account_id = cfg["account_id"]

            # 取帳戶現金
            cash_row = db.execute(text("""
                SELECT cash FROM strategy_accounts WHERE id=:id
            """), {"id": account_id}).fetchone()
            cash = float(cash_row[0] or 200000) if cash_row else 200000

            # 取現有持股數
            positions = db.execute(text("""
                SELECT code, lots, avg_cost FROM positions WHERE account_id=:id
            """), {"id": account_id}).fetchall()
            pos_map = {r[0]: {"lots": r[1], "avg_cost": float(r[2] or 0)} for r in positions}
            pos_count = len(pos_map)

            # 取市場狀況
            mkt = db.execute(text("""
                SELECT trend_regime, breadth_score, market_bias_score
                FROM market_context_daily WHERE context_date=:d
            """), {"d": str(signal_date)}).fetchone()
            market_risk = "high" if mkt and float(mkt[2] or 50) < 40 else "low"

            # 取候選股（依帳戶規則篩選）
            candidates = _get_candidates(db, cfg, signal_date)

            # 產生買入決策
            rank = 0
            rank_limit = cfg["candidate_rank_limit"] or 5
            for cand in candidates:
                if rank >= rank_limit:
                    break
                rank += 1
                code = cand["code"]
                name = cand["name"]
                score = cand["final_score"]
                ref_price = cand["close"]
                rsi = cand.get("rsi14", 50)
                ma_dist = cand.get("distance_ma20", 0)

                # 決策邏輯
                action = "BUY"
                blocked = False
                blocked_reason = None

                # 現金不足
                max_amount = cash * cfg["max_position_pct"]
                if max_amount < ref_price * 10:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = "現金不足（最大部位 < 10 股）"

                # 已達最大持股
                elif pos_count >= cfg["max_positions"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"已達最大持股數 {cfg['max_positions']}"

                # RSI 過熱
                elif rsi and float(rsi) > cfg["max_rsi14"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"RSI={rsi:.0f} > {cfg['max_rsi14']}"

                # RSI 過低
                elif rsi and float(rsi) < cfg["min_rsi14"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"RSI={rsi:.0f} < {cfg['min_rsi14']}"

                # MA20 距離過遠
                elif abs(float(ma_dist or 0)) > cfg["max_distance_ma20_pct"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"離MA20={ma_dist:.1f}% > {cfg['max_distance_ma20_pct']}%"

                # 計算建議股數
                suggested_shares = 0
                expected_fill = ref_price
                stop_loss = round(ref_price * (1 - cfg["stop_loss_pct"]), 2)
                target = round(ref_price * (1 + cfg["take_profit_pct"]), 2)

                if not blocked and ref_price > 0:
                    max_loss = 2000  # 單筆最大虧損
                    shares_by_risk = int(max_loss / (ref_price * cfg["stop_loss_pct"]))
                    shares_by_capital = int(max_amount / ref_price)
                    suggested_shares = max(1, min(shares_by_risk, shares_by_capital))
                    expected_fill = round(ref_price * (1 + COST_SLIPPAGE_BUY), 2)

                reason = f"分數{score:.1f}，排名{rank}" if not blocked else blocked_reason

                db.execute(text("""
                    INSERT INTO strategy_decision_logs
                        (account_id, strategy_name, mode, signal_date,
                         data_cutoff_time, execution_date, execution_time_model,
                         code, action, candidate_score, final_score, risk_score,
                         suggested_shares, reference_price, expected_fill_price,
                         stop_loss, target_price, is_blocked, blocked_reason,
                         reason_summary, no_lookahead_pass, created_at)
                    VALUES
                        (:aid, :sn, :mode, :sd,
                         :dct, :ed, 'next_day_open_slippage',
                         :code, :action, :cs, :fs, :rs,
                         :shares, :ref, :fill,
                         :sl, :tp, :blocked, :br,
                         :reason, 1, datetime('now','localtime'))
                """), {
                    "aid": account_id,
                    "sn": cfg["strategy_name"],
                    "mode": cfg["mode"],
                    "sd": str(signal_date),
                    "dct": f"{signal_date} 18:00:00",
                    "ed": execution_date,
                    "code": code,
                    "action": action,
                    "cs": score,
                    "fs": score,
                    "rs": cand.get("risk_score", 30),
                    "shares": suggested_shares,
                    "ref": ref_price,
                    "fill": expected_fill,
                    "sl": stop_loss,
                    "tp": target,
                    "blocked": 1 if blocked else 0,
                    "br": blocked_reason,
                    "reason": reason,
                })
                decisions_written += 1

            # 產生賣出決策（停損/停利/持有天數）
            for code, pos in pos_map.items():
                sell_price_row = db.execute(text("""
                    SELECT close FROM ohlcv_daily
                    WHERE code=:c AND trade_date=:d
                """), {"c": code, "d": str(signal_date)}).fetchone()

                if not sell_price_row:
                    continue
                sell_price = float(sell_price_row[0])
                avg_cost = pos["avg_cost"]
                if avg_cost <= 0:
                    continue

                pnl_pct = (sell_price / avg_cost - 1) * 100
                sell_action = None
                sell_reason = None

                if pnl_pct <= -cfg["stop_loss_pct"] * 100:
                    sell_action = "SELL"
                    sell_reason = f"觸發停損（-{cfg['stop_loss_pct']*100:.0f}%），目前虧損{pnl_pct:.1f}%"
                elif pnl_pct >= cfg["take_profit_pct"] * 100:
                    sell_action = "SELL"
                    sell_reason = f"達到停利（+{cfg['take_profit_pct']*100:.0f}%），目前獲利{pnl_pct:.1f}%"

                if sell_action:
                    db.execute(text("""
                        INSERT INTO strategy_decision_logs
                            (account_id, strategy_name, mode, signal_date,
                             execution_date, code, action,
                             reference_price, is_blocked, reason_summary,
                             no_lookahead_pass, created_at)
                        VALUES
                            (:aid, :sn, :mode, :sd,
                             :ed, :code, 'SELL',
                             :ref, 0, :reason,
                             1, datetime('now','localtime'))
                    """), {
                        "aid": account_id, "sn": cfg["strategy_name"],
                        "mode": cfg["mode"], "sd": str(signal_date),
                        "ed": execution_date, "code": code,
                        "ref": sell_price, "reason": sell_reason,
                    })
                    decisions_written += 1

        db.commit()
        logger.success(f"[V5] {signal_date} 決策完成，共 {decisions_written} 筆")
        return {"ok": True, "signal_date": str(signal_date),
                "execution_date": execution_date,
                "decisions": decisions_written}

    except Exception as e:
        db.rollback()
        logger.error(f"[V5] 決策失敗: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


def _get_candidates(db, cfg: dict, signal_date: date) -> list[dict]:
    """根據策略設定取候選股"""
    # A7 MLTop5：從 ml_score_results 選股，不走 final_score 邏輯
    if cfg.get("strategy_name") == "MLTop5":
        rows = db.execute(text("""
            SELECT m.code, sm.name, 'LIQUID_MOMENTUM',
                   m.ml_score, 30.0, 50.0,
                   tdf.rsi14, tdf.distance_ma20, tdf.return_5d,
                   o.close
            FROM ml_score_results m
            LEFT JOIN stock_meta sm ON sm.code=m.code
            LEFT JOIN technical_daily_features tdf
                   ON tdf.code=m.code AND tdf.trade_date=:sd
            LEFT JOIN ohlcv_daily o
                   ON o.code=m.code AND o.trade_date=:sd
            WHERE m.score_date=(
                SELECT MAX(score_date) FROM ml_score_results WHERE score_date<=:sd
            )
              AND m.ml_rank <= 5
              AND o.close IS NOT NULL AND o.close >= 10
            ORDER BY m.ml_rank ASC
            LIMIT 5
        """), {"sd": str(signal_date)}).fetchall()
        return [{
            "code": r[0], "name": r[1] or r[0], "stock_class": r[2],
            "final_score": float(r[3] or 0), "risk_score": float(r[4] or 30),
            "momentum_score": float(r[5] or 50),
            "rsi14": float(r[6] or 50), "distance_ma20": float(r[7] or 0),
            "return_5d": float(r[8] or 0), "close": float(r[9] or 0),
        } for r in rows if r[9]]
    conditions = [
        "ds.score_date=:sd",
        "ds.final_action IN ('BUY','WATCH')",
        "o.close IS NOT NULL",
        "o.close >= 10",
        f"(tdf.rsi14 IS NULL OR (tdf.rsi14 >= {cfg['min_rsi14']} AND tdf.rsi14 < {cfg['max_rsi14']}))",
        f"(tdf.distance_ma20 IS NULL OR ABS(tdf.distance_ma20) < {cfg['max_distance_ma20_pct']})",
        f"ds.final_score >= {cfg['min_score']}",
    ]

    # 大型股過濾
    if cfg.get("large_cap_only"):
        conditions.append("ds.stock_class IN ('CORE_LARGE_CAP','LARGE_LIQUID')")
    else:
        conditions.append("ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','NORMAL')")

    # 主題過濾
    theme_filter = cfg.get("theme_filter")
    theme_join = ""
    if theme_filter:
        themes = [t.strip() for t in theme_filter.split(",")]
        theme_placeholders = ",".join(f"'{t}'" for t in themes)
        # theme_trend_daily 用 leader_codes 欄位
        theme_likes = " OR ".join(
            f"ttd.leader_codes LIKE '%'||ds.code||'%'"
            for _ in themes
        )
        conditions.append(f"""
            ds.code IN (
                SELECT DISTINCT ds2.code FROM daily_scores ds2
                WHERE ds2.score_date=:sd
                AND EXISTS (
                    SELECT 1 FROM theme_trend_daily ttd
                    WHERE ttd.theme IN ({theme_placeholders})
                    AND ttd.context_date=:sd
                    AND ttd.leader_codes LIKE '%' || ds2.code || '%'
                )
            )
        """)

    where = " AND ".join(conditions)
    order = """
        CASE ds.stock_class WHEN 'CORE_LARGE_CAP' THEN 1
            WHEN 'LARGE_LIQUID' THEN 2 ELSE 3 END,
        ds.final_score DESC
    """

    rows = db.execute(text(f"""
        SELECT ds.code, sm.name, ds.stock_class,
               ds.final_score, ds.risk_score, ds.momentum_score,
               tdf.rsi14, tdf.distance_ma20, tdf.return_5d,
               o.close
        FROM daily_scores ds
        LEFT JOIN stock_meta sm ON sm.code=ds.code
        LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code AND tdf.trade_date=:sd
        LEFT JOIN ohlcv_daily o ON o.code=ds.code AND o.trade_date=:sd
        WHERE {where}
        ORDER BY {order}
        LIMIT 20
    """), {"sd": str(signal_date)}).fetchall()

    return [{
        "code": r[0], "name": r[1], "stock_class": r[2],
        "final_score": float(r[3] or 0), "risk_score": float(r[4] or 30),
        "momentum_score": float(r[5] or 50),
        "rsi14": float(r[6] or 50), "distance_ma20": float(r[7] or 0),
        "return_5d": float(r[8] or 0),
        "close": float(r[9] or 0),
    } for r in rows]
