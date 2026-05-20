from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def _to_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def calc_metrics(account_id: int, db: Session) -> dict:
    """
    P3 strategy performance metrics.

    Metrics:
    - trade_count / total_trades
    - win_rate
    - max_drawdown
    - profit_factor

    Note:
    - realized trades are based on SELL rows in trade_logs.
    - max drawdown is based on equity_curve.total_equity.
    """

    sells = db.execute(
        text("""
            SELECT pnl
            FROM trade_logs
            WHERE account_id = :account_id
              AND direction = 'SELL'
              AND pnl IS NOT NULL
        """),
        {"account_id": account_id},
    ).fetchall()

    pnls = [_to_float(r[0]) for r in sells]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]

    trade_count = len(pnls)
    winning_trades = len(wins)
    losing_trades = len(losses)

    gross_profit = round(sum(wins), 2) if wins else 0.0
    gross_loss = round(sum(losses), 2) if losses else 0.0
    realized_pnl = round(sum(pnls), 2) if pnls else 0.0

    win_rate = round(winning_trades / trade_count * 100, 2) if trade_count else 0.0
    avg_profit = round(gross_profit / winning_trades, 2) if winning_trades else 0.0
    avg_loss = round(gross_loss / losing_trades, 2) if losing_trades else 0.0

    if gross_loss < 0:
        profit_factor = round(gross_profit / abs(gross_loss), 2)
    elif gross_profit > 0:
        profit_factor = None
    else:
        profit_factor = 0.0

    eq_rows = db.execute(
        text("""
            SELECT total_equity
            FROM equity_curve
            WHERE account_id = :account_id
            ORDER BY snap_date ASC
        """),
        {"account_id": account_id},
    ).fetchall()

    max_drawdown = 0.0
    if eq_rows:
        peak = None
        for row in eq_rows:
            equity = _to_float(row[0])
            if equity <= 0:
                continue

            if peak is None or equity > peak:
                peak = equity

            if peak and peak > 0:
                dd = (peak - equity) / peak * 100
                if dd > max_drawdown:
                    max_drawdown = dd

    max_drawdown = round(max_drawdown, 2)

    return {
        "account_id": account_id,

        "trade_count": trade_count,
        "total_trades": trade_count,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,

        "win_rate": win_rate,
        "max_drawdown": max_drawdown,
        "profit_factor": profit_factor,

        "realized_pnl": realized_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_profit": avg_profit,
        "avg_loss": avg_loss,

        "avg_holding_days": 0.0,
    }
