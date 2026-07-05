"""
簡易策略回測引擎(研究室用)
單一股票 + 停損/停利/持有天數規則 vs 同檔買進持有。
紀律:T 收盤判斷、T+1 開盤成交、含手續費與證交稅。
限制(誠實聲明):
- 非嚴謹框架:無選股、無組合、無樣本外驗證
- 歷史段未還原除權息:除息跳空可能誤觸停損
- 禁止用來調參數(curve fitting)
"""
from sqlalchemy import text
from backend.models.database import SessionLocal

FEE = 0.001425
TAX = 0.003


def run_simple_backtest(code: str, start: str, end: str,
                        stop_pct: float = 8.0, take_pct: float = 15.0,
                        max_hold: int = 0, initial_cash: float = 200000.0):
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT trade_date, open, close FROM ohlcv_daily
            WHERE code=:c AND trade_date BETWEEN :s AND :e
              AND open IS NOT NULL AND open>0 AND close IS NOT NULL AND close>0
            ORDER BY trade_date
        """), {"c": code, "s": start, "e": end}).fetchall()
    finally:
        db.close()
    if len(rows) < 20:
        return {"error": f"{code} 在區間內資料不足(僅 {len(rows)} 天)"}

    cash = initial_cash
    shares = 0
    entry_price = 0.0
    hold_days = 0
    pending_buy = True
    pending_sell = False
    sell_reason = None
    trades = []
    curve = []
    total_fees = 0.0
    wins = 0

    first_open = float(rows[0][1])
    bh_shares = int(initial_cash / (first_open * (1 + FEE)))
    bh_cash = initial_cash - bh_shares * first_open * (1 + FEE)

    for d, o, c in rows:
        d = str(d); o = float(o); c = float(c)

        # ── 開盤:執行昨日收盤的決定 ──
        if pending_sell and shares > 0:
            gross = shares * o
            cost = gross * (FEE + TAX)
            cash += gross - cost
            total_fees += cost
            pnl = (o - entry_price) * shares - cost
            if pnl > 0:
                wins += 1
            trades.append({"date": d, "action": "SELL", "price": round(o, 2),
                           "shares": shares, "pnl": round(pnl), "reason": sell_reason})
            shares = 0
            pending_sell = False
            pending_buy = True   # 隔日開盤再進場
        elif pending_buy and shares == 0:
            buy_sh = int(cash / (o * (1 + FEE)))
            if buy_sh > 0:
                fee = buy_sh * o * FEE
                cash -= buy_sh * o + fee
                total_fees += fee
                shares = buy_sh
                entry_price = o
                hold_days = 0
                trades.append({"date": d, "action": "BUY", "price": round(o, 2),
                               "shares": buy_sh, "pnl": None, "reason": "進場"})
            pending_buy = False

        # ── 收盤:判斷賣出條件(T+1 開盤執行)──
        if shares > 0 and not pending_sell:
            hold_days += 1
            ret = (c - entry_price) / entry_price * 100
            if max_hold and hold_days >= max_hold:
                pending_sell, sell_reason = True, f"到期({max_hold}日)"
            elif ret <= -abs(stop_pct):
                pending_sell, sell_reason = True, f"停損({ret:.1f}%)"
            elif ret >= abs(take_pct):
                pending_sell, sell_reason = True, f"停利({ret:.1f}%)"

        curve.append({"date": d,
                      "equity": round(cash + shares * c, 0),
                      "bh_equity": round(bh_cash + bh_shares * c, 0)})

    final = curve[-1]["equity"]
    bh_final = curve[-1]["bh_equity"]
    peak = -1e18
    max_dd = 0.0
    for p in curve:
        peak = max(peak, p["equity"])
        if peak > 0:
            max_dd = min(max_dd, (p["equity"] / peak - 1) * 100)

    sell_trades = [t for t in trades if t["action"] == "SELL"]
    return {
        "params": {"code": code, "start": start, "end": end,
                   "stop_pct": stop_pct, "take_pct": take_pct,
                   "max_hold": max_hold, "initial_cash": initial_cash},
        "stats": {
            "total_return": round((final / initial_cash - 1) * 100, 2),
            "buy_hold_return": round((bh_final / initial_cash - 1) * 100, 2),
            "excess_vs_bh": round((final - bh_final) / initial_cash * 100, 2),
            "max_drawdown": round(max_dd, 2),
            "trade_count": len(sell_trades),
            "win_rate": round(wins / len(sell_trades) * 100, 1) if sell_trades else 0,
            "total_fees": round(total_fees, 0),
            "days": len(curve),
        },
        "warning": "未還原除權息;非嚴謹框架;請勿用於調參(curve fitting)",
        "equity_curve": curve,
        "trades": trades[-100:],
    }
