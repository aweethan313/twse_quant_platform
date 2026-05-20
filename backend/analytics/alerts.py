from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def _to_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _to_int(x, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def get_latest_alerts(
    db: Session,
    days: int = 20,
    volume_multiple: float = 2.0,
    limit: int = 60,
    as_of: Optional[str] = None,
) -> dict:
    """
    V4.7.2 P1 alert system.

    No-lookahead rule:
    - current row = latest trade_date <= as_of
    - N-day high/low/volume average only uses rows BEFORE current row.
    """

    days = max(5, min(int(days), 120))
    volume_multiple = max(1.1, float(volume_multiple))
    limit = max(1, min(int(limit), 200))

    if as_of:
        latest_row = db.execute(
            text("""
                SELECT MAX(trade_date)
                FROM ohlcv_daily
                WHERE trade_date <= :as_of
            """),
            {"as_of": as_of},
        ).fetchone()
    else:
        latest_row = db.execute(
            text("SELECT MAX(trade_date) FROM ohlcv_daily")
        ).fetchone()

    latest_date = latest_row[0] if latest_row and latest_row[0] else None
    if not latest_date:
        return {
            "available": False,
            "trade_date": None,
            "days": days,
            "volume_multiple": volume_multiple,
            "alerts": [],
        }

    rows = db.execute(
        text("""
            WITH ranked AS (
                SELECT
                    o.code,
                    COALESCE(sm.name, o.code) AS name,
                    o.trade_date,
                    o.open,
                    o.high,
                    o.low,
                    o.close,
                    o.volume,
                    o.change_pct,
                    ROW_NUMBER() OVER (
                        PARTITION BY o.code
                        ORDER BY o.trade_date DESC
                    ) AS rn
                FROM ohlcv_daily o
                LEFT JOIN stock_meta sm ON sm.code = o.code
                WHERE o.trade_date <= :latest_date
            )
            SELECT
                code, name, trade_date, open, high, low, close,
                volume, change_pct, rn
            FROM ranked
            WHERE rn <= :need_rows
            ORDER BY code ASC, rn ASC
        """),
        {"latest_date": str(latest_date), "need_rows": days + 1},
    ).fetchall()

    by_code: dict[str, list[dict]] = {}
    for r in rows:
        code = str(r[0])
        by_code.setdefault(code, []).append({
            "code": code,
            "name": r[1],
            "trade_date": str(r[2]),
            "open": _to_float(r[3]),
            "high": _to_float(r[4]),
            "low": _to_float(r[5]),
            "close": _to_float(r[6]),
            "volume": _to_int(r[7]),
            "change_pct": _to_float(r[8]),
            "rn": int(r[9]),
        })

    alerts = []

    for code, items in by_code.items():
        if not items:
            continue

        current = items[0]

        # 只看最新交易日，避免 stale / 缺資料股票混進今日警示
        if current["trade_date"] != str(latest_date):
            continue

        hist = items[1:]
        if len(hist) < max(5, days // 2):
            continue

        highs = [x["high"] for x in hist if x["high"] > 0]
        lows = [x["low"] for x in hist if x["low"] > 0]
        vols = [x["volume"] for x in hist if x["volume"] > 0]

        prev_high = max(highs) if highs else None
        prev_low = min(lows) if lows else None
        avg_vol = sum(vols) / len(vols) if vols else 0

        close = current["close"]
        volume = current["volume"]
        change_pct = current["change_pct"]

        if prev_high is not None and prev_high > 0 and close > prev_high:
            strength = (close / prev_high - 1) * 100
            alerts.append({
                "type": "breakout_high",
                "label": f"突破 {days} 日高",
                "code": code,
                "name": current["name"],
                "trade_date": current["trade_date"],
                "close": round(close, 2),
                "change_pct": round(change_pct, 2),
                "reference": round(prev_high, 2),
                "strength": round(strength, 2),
                "message": f"{code} {current['name']} 收盤 {close:.2f} 突破前 {days} 日高點 {prev_high:.2f}",
            })

        if prev_low is not None and prev_low > 0 and close > 0 and close < prev_low:
            strength = (prev_low / close - 1) * 100
            alerts.append({
                "type": "breakdown_low",
                "label": f"跌破 {days} 日低",
                "code": code,
                "name": current["name"],
                "trade_date": current["trade_date"],
                "close": round(close, 2),
                "change_pct": round(change_pct, 2),
                "reference": round(prev_low, 2),
                "strength": round(strength, 2),
                "message": f"{code} {current['name']} 收盤 {close:.2f} 跌破前 {days} 日低點 {prev_low:.2f}",
            })
        if avg_vol > 0 and volume >= avg_vol * volume_multiple:
            vol_ratio = volume / avg_vol
            alerts.append({
                "type": "volume_spike",
                "label": "爆量",
                "code": code,
                "name": current["name"],
                "trade_date": current["trade_date"],
                "close": round(close, 2),
                "change_pct": round(change_pct, 2),
                "volume": int(volume),
                "avg_volume": int(avg_vol),
                "volume_ratio": round(vol_ratio, 2),
                "strength": round(vol_ratio, 2),
                "message": f"{code} {current['name']} 爆量，成交量為前 {days} 日均量 {vol_ratio:.2f} 倍",
            })

    type_rank = {
        "breakout_high": 3,
        "volume_spike": 2,
        "breakdown_low": 1,
    }

    alerts.sort(
        key=lambda x: (
            type_rank.get(x["type"], 0),
            abs(_to_float(x.get("strength"))),
            abs(_to_float(x.get("change_pct"))),
        ),
        reverse=True,
    )

    return {
        "available": True,
        "trade_date": str(latest_date),
        "days": days,
        "volume_multiple": volume_multiple,
        "count": len(alerts),
        "alerts": alerts[:limit],
    }
