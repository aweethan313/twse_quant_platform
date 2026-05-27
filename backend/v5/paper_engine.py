"""backend/v5/paper_engine.py
V5 Paper Trading 執行引擎
- simulate_paper_fills: 把決策轉成實際虛擬成交
- update_positions: 更新持倉
- update_equity: 更新 equity_curve
"""
from __future__ import annotations
from datetime import date, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

FEE_RATE   = 0.001425 * 0.38   # 手續費（折扣後）
TAX_RATE   = 0.003               # 證交稅（賣出）
SLIP_BUY   = 0.002               # 買進滑價
SLIP_SELL  = 0.003               # 賣出滑價
MIN_FEE    = 20                  # 最低手續費


def simulate_paper_fills(execution_date: date = None) -> dict:
    """
    模擬 T+1 成交：用 execution_date 的開盤價（或收盤價 fallback）成交
    把 strategy_decision_logs 的 BUY/SELL 轉成 paper_fills + 更新 positions + cash
    """
    if execution_date is None:
        execution_date = date.today()

    db = SessionLocal()
    filled = 0
    errors = []

    try:
        # 找未成交的 BUY/SELL 決策（execution_date = today）
        decisions = db.execute(text("""
            SELECT id, account_id, strategy_name, signal_date, execution_date,
                   code, action, suggested_shares, expected_fill_price,
                   stop_loss, target_price
            FROM strategy_decision_logs
            WHERE execution_date=:ed
              AND action IN ('BUY','SELL')
              AND is_blocked=0
              AND id NOT IN (
                  SELECT COALESCE(plan_id, -1) FROM paper_fills
                  WHERE execution_date=:ed
              )
            ORDER BY account_id, action DESC
        """), {"ed": str(execution_date)}).fetchall()

        if not decisions:
            logger.info(f"[PAPER] {execution_date} 無待成交決策")
            return {"ok": True, "filled": 0, "message": "無待成交決策"}

        # 內存現金追蹤（避免同批次超額買入）
        cash_tracker = {}

        for dec in decisions:
            dec_id, aid, sname, sig_date, exec_date, code, action, shares, exp_fill, sl, tp = dec

            # 取成交價：優先 open，fallback close
            price_row = db.execute(text("""
                SELECT open, close FROM ohlcv_daily
                WHERE code=:c AND trade_date=:d
            """), {"c": code, "d": str(execution_date)}).fetchone()

            if not price_row:
                errors.append(f"{code} 無 {execution_date} 價格")
                continue

            open_price = float(price_row[0] or price_row[1])
            close_price = float(price_row[1] or price_row[0])
            base_price = open_price if open_price > 0 else close_price

            # 取帳戶現金
            acct = db.execute(text(
                "SELECT cash, initial_cash FROM strategy_accounts WHERE id=:id"
            ), {"id": aid}).fetchone()
            _db_cash = float(acct[0] or acct[1] or 200000) if acct else 200000
            cash = cash_tracker.get(aid, _db_cash)  # 用 tracker 追蹤本輪已扣現金

            if action == "BUY":
                fill_price = round(base_price * (1 + SLIP_BUY), 2)
                shares_int = int(shares or 0)
                if shares_int <= 0:
                    # 自動計算股數
                    max_amount = cash * 0.20
                    shares_int = max(1, int(max_amount / fill_price))

                gross = fill_price * shares_int
                fee = max(MIN_FEE, round(gross * FEE_RATE, 0))
                total_cost = gross + fee

                if total_cost > cash:
                    # 縮減股數
                    shares_int = max(1, int((cash - MIN_FEE) / (fill_price * (1 + FEE_RATE))))
                    gross = fill_price * shares_int
                    fee = max(MIN_FEE, round(gross * FEE_RATE, 0))
                    total_cost = gross + fee

                if shares_int <= 0 or total_cost > cash:
                    logger.debug(f"[PAPER] A{aid} {code} SKIP 現金不足（已滿倉）")
                    continue

                # 更新現金
                db.execute(text(
                    "UPDATE strategy_accounts SET cash=cash-:cost WHERE id=:id"
                ), {"cost": total_cost, "id": aid})
                cash_tracker[aid] = cash_tracker.get(aid, cash) - total_cost

                # 更新持倉
                existing_pos = db.execute(text(
                    "SELECT id, lots, avg_cost FROM positions WHERE account_id=:id AND code=:c"
                ), {"id": aid, "c": code}).fetchone()

                if existing_pos:
                    old_lots = float(existing_pos[1] or 0)
                    old_cost = float(existing_pos[2] or 0)
                    new_lots = old_lots + shares_int
                    new_cost = (old_lots * old_cost + shares_int * fill_price) / new_lots
                    db.execute(text("""
                        UPDATE positions SET lots=:lots, avg_cost=:cost
                        WHERE id=:pid
                    """), {"lots": new_lots, "cost": new_cost, "pid": existing_pos[0]})
                else:
                    db.execute(text("""
                        INSERT INTO positions (account_id, code, lots, avg_cost, opened_at)
                        VALUES (:id, :c, :lots, :cost, datetime('now','localtime'))
                    """), {"id": aid, "c": code, "lots": shares_int,
                           "cost": fill_price})

                # 寫 paper_fills
                db.execute(text("""
                    INSERT INTO paper_fills
                        (account_id, plan_id, strategy_name, signal_date, execution_date,
                         code, action, shares, fill_price, fill_time, fill_source,
                         execution_time_model, fee, tax, gross_amount, net_amount, no_lookahead_pass)
                    VALUES
                        (:aid, :pid, :sn, :sd, :ed,
                         :code, 'BUY', :shares, :fp, :ft, 'simulated',
                         'next_day_open_slippage', :fee, 0, :gross, :net, 1)
                """), {
                    "aid": aid, "pid": dec_id, "sn": sname, "sd": sig_date, "ed": str(execution_date),
                    "code": code, "shares": shares_int, "fp": fill_price,
                    "ft": f"{execution_date} 09:10:00",
                    "fee": fee, "gross": gross, "net": total_cost,
                })
                filled += 1
                logger.info(f"[PAPER] A{aid} BUY {code} {shares_int}股 @{fill_price:.2f} 費={fee:.0f}")

            elif action == "SELL":
                pos = db.execute(text(
                    "SELECT id, lots, avg_cost FROM positions WHERE account_id=:id AND code=:c"
                ), {"id": aid, "c": code}).fetchone()

                if not pos or float(pos[1] or 0) <= 0:
                    continue

                sell_lots = float(pos[1])
                avg_cost = float(pos[2] or 0)
                fill_price = round(base_price * (1 - SLIP_SELL), 2)
                gross = fill_price * sell_lots
                fee = max(MIN_FEE, round(gross * FEE_RATE, 0))
                tax = round(gross * TAX_RATE, 0)
                net_proceeds = gross - fee - tax
                pnl = net_proceeds - avg_cost * sell_lots

                # 更新現金
                db.execute(text(
                    "UPDATE strategy_accounts SET cash=cash+:proc WHERE id=:id"
                ), {"proc": net_proceeds, "id": aid})
                cash_tracker[aid] = cash_tracker.get(aid, cash) + net_proceeds

                # 移除持倉
                db.execute(text(
                    "DELETE FROM positions WHERE id=:pid"
                ), {"pid": pos[0]})

                # 寫 paper_fills
                db.execute(text("""
                    INSERT INTO paper_fills
                        (account_id, plan_id, strategy_name, signal_date, execution_date,
                         code, action, shares, fill_price, fill_time, fill_source,
                         execution_time_model, fee, tax, gross_amount, net_amount, note, no_lookahead_pass)
                    VALUES
                        (:aid, :pid, :sn, :sd, :ed,
                         :code, 'SELL', :shares, :fp, :ft, 'simulated',
                         'next_day_open_slippage', :fee, :tax, :gross, :net, :note, 1)
                """), {
                    "aid": aid, "pid": dec_id, "sn": sname, "sd": sig_date, "ed": str(execution_date),
                    "code": code, "shares": int(sell_lots), "fp": fill_price,
                    "ft": f"{execution_date} 09:10:00",
                    "fee": fee, "tax": tax, "gross": gross, "net": net_proceeds,
                    "note": f"PnL={pnl:+.0f}",
                })
                filled += 1
                logger.info(f"[PAPER] A{aid} SELL {code} {sell_lots:.0f}股 @{fill_price:.2f} PnL={pnl:+.0f}")

        db.commit()
        logger.success(f"[PAPER] {execution_date} 成交 {filled} 筆，錯誤 {len(errors)} 筆")
        return {"ok": True, "filled": filled, "errors": errors}

    except Exception as e:
        db.rollback()
        logger.error(f"[PAPER] 執行失敗: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


def update_v5_equity(snap_date: date = None) -> dict:
    """更新 V5 帳戶的 equity_curve"""
    if snap_date is None:
        snap_date = date.today()

    db = SessionLocal()
    updated = 0

    try:
        accounts = db.execute(text(
            "SELECT id, cash, initial_cash FROM strategy_accounts WHERE id >= 11"
        )).fetchall()

        for aid, cash, init_cash in accounts:
            cash_f = float(cash or init_cash or 200000)
            init_f = float(init_cash or 200000)

            # 計算持倉市值
            mkt = db.execute(text("""
                SELECT SUM(p.lots * o.close)
                FROM positions p
                LEFT JOIN ohlcv_daily o ON o.code=p.code
                    AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
                WHERE p.account_id=:id
            """), {"id": aid}).scalar() or 0

            total = cash_f + float(mkt)

            # 昨日 equity
            prev = db.execute(text("""
                SELECT total_equity FROM equity_curve
                WHERE account_id=:id AND snap_date < :d
                ORDER BY snap_date DESC LIMIT 1
            """), {"id": aid, "d": str(snap_date)}).scalar()

            daily_ret = (total / float(prev) - 1) * 100 if prev and float(prev) > 0 else 0

            db.execute(text("""
                INSERT INTO equity_curve
                    (account_id, snap_date, cash, market_value, total_equity, daily_return)
                VALUES (:id, :d, :c, :m, :t, :r)
                ON CONFLICT(account_id, snap_date) DO UPDATE SET
                    cash=excluded.cash, market_value=excluded.market_value,
                    total_equity=excluded.total_equity, daily_return=excluded.daily_return
            """), {
                "id": aid, "d": str(snap_date),
                "c": cash_f, "m": float(mkt), "t": total, "r": round(daily_ret, 4),
            })
            updated += 1

        db.commit()
        logger.success(f"[PAPER] {snap_date} equity 更新 {updated} 個帳戶")
        return {"ok": True, "updated": updated}

    except Exception as e:
        db.rollback()
        logger.error(f"[PAPER] equity 更新失敗: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


def check_stop_loss_take_profit(signal_date: date = None) -> dict:
    """檢查所有 V5 帳戶持倉是否觸發停損/停利，產生 SELL 決策"""
    if signal_date is None:
        signal_date = date.today()

    db = SessionLocal()
    sells_generated = 0

    try:
        next_day = db.execute(text("""
            SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > :d
        """), {"d": str(signal_date)}).scalar()
        execution_date = str(next_day) if next_day else str(signal_date + timedelta(days=1))

        accounts = db.execute(text("""
            SELECT a.id, a.name, cfg.stop_loss_pct, cfg.take_profit_pct, cfg.strategy_name
            FROM strategy_accounts a
            JOIN strategy_account_configs cfg ON cfg.account_id=a.id
            WHERE a.id >= 11
        """)).fetchall()

        for aid, aname, sl_pct, tp_pct, sname in accounts:
            positions = db.execute(text("""
                SELECT p.code, p.lots, p.avg_cost, o.close
                FROM positions p
                LEFT JOIN ohlcv_daily o ON o.code=p.code AND o.trade_date=:d
                WHERE p.account_id=:id AND p.lots > 0
            """), {"id": aid, "d": str(signal_date)}).fetchall()

            for code, lots, avg_cost, close in positions:
                if not close or not avg_cost: continue
                pnl_pct = (float(close) / float(avg_cost) - 1) * 100

                reason = None
                if pnl_pct <= -(sl_pct or 0.08) * 100:
                    reason = f"停損 ({pnl_pct:.1f}% <= -{(sl_pct or 0.08)*100:.0f}%)"
                elif pnl_pct >= (tp_pct or 0.15) * 100:
                    reason = f"停利 ({pnl_pct:.1f}% >= +{(tp_pct or 0.15)*100:.0f}%)"

                if reason:
                    # 避免重複
                    exists = db.execute(text("""
                        SELECT id FROM strategy_decision_logs
                        WHERE account_id=:id AND code=:c AND signal_date=:sd AND action='SELL'
                    """), {"id": aid, "c": code, "sd": str(signal_date)}).fetchone()

                    if not exists:
                        db.execute(text("""
                            INSERT INTO strategy_decision_logs
                                (account_id, strategy_name, mode, signal_date, execution_date,
                                 code, action, reference_price, is_blocked, reason_summary,
                                 no_lookahead_pass, created_at)
                            VALUES (:aid, :sn, 'forward_paper', :sd, :ed,
                                    :code, 'SELL', :ref, 0, :reason, 1,
                                    datetime('now','localtime'))
                        """), {
                            "aid": aid, "sn": sname, "sd": str(signal_date), "ed": execution_date,
                            "code": code, "ref": float(close), "reason": reason,
                        })
                        sells_generated += 1
                        logger.info(f"[PAPER] A{aid} {code} {reason}")

        db.commit()
        return {"ok": True, "sells_generated": sells_generated}

    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
