"""
backend/services/technical_features.py
技術指標計算：MA5/10/20/60, RSI14, MACD, ATR, 報酬率
從 ohlcv_daily 計算，不偷看未來（rolling window）
"""
from __future__ import annotations
import math
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def _calc_ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n: return None
    return sum(closes[-n:]) / n


def _calc_rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15: return None
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, 15)]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, 15)]
    ag, al = sum(gains)/14, sum(losses)/14
    if al == 0: return 100.0
    return round(100 - 100/(1 + ag/al), 2)


def _calc_atr14(highs, lows, closes) -> float | None:
    if len(closes) < 15: return None
    trs = []
    for i in range(1, 15):
        tr = max(
            highs[-(15-i)] - lows[-(15-i)],
            abs(highs[-(15-i)] - closes[-(15-i+1)]),
            abs(lows[-(15-i)] - closes[-(15-i+1)]),
        )
        trs.append(tr)
    return round(sum(trs)/14, 4)


def _calc_macd(closes: list[float]) -> tuple[float|None, float|None, float|None]:
    """MACD(12,26,9)"""
    if len(closes) < 35: return None, None, None

    def ema(data, n):
        k = 2/(n+1)
        e = data[0]
        for v in data[1:]:
            e = v*k + e*(1-k)
        return e

    ema12 = ema(closes[-35:], 12)
    ema26 = ema(closes[-35:], 26)
    macd = ema12 - ema26

    # Signal (9-day EMA of MACD) - simplified
    return round(macd, 4), None, None


def build_technical_features(
    target_date: date = None,
    codes: list[str] = None,
    lookback_days: int = 120,
) -> int:
    """
    為所有股票計算 target_date 的技術指標
    只使用 <= target_date 的資料（不偷看未來）
    """
    if target_date is None:
        target_date = date.today()

    db = SessionLocal()
    updated = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # 取所有要計算的股票
        if codes:
            code_filter = "AND o.code IN (" + ",".join(f"'{c}'" for c in codes) + ")"
        else:
            code_filter = ""

        # 每次抓一批股票的歷史資料
        code_rows = db.execute(text(f"""
            SELECT DISTINCT code FROM ohlcv_daily
            WHERE trade_date=:d {code_filter}
        """), {"d": str(target_date)}).fetchall()

        for (code,) in code_rows:
            # 抓最近 lookback_days 天的資料（含 target_date）
            rows = db.execute(text("""
                SELECT trade_date, open, high, low, close, volume
                FROM ohlcv_daily
                WHERE code=:c AND trade_date<=:d
                  AND close IS NOT NULL AND close > 0
                ORDER BY trade_date DESC
                LIMIT :n
            """), {"c": code, "d": str(target_date), "n": lookback_days}).fetchall()

            if not rows:
                continue

            # 按日期正序
            rows = list(reversed(rows))
            closes  = [float(r[2]) for r in rows]  # high
            closes_c = [float(r[4]) for r in rows]  # close
            highs   = [float(r[2]) for r in rows]
            lows    = [float(r[3]) for r in rows]
            volumes = [float(r[5] or 0) for r in rows]

            c = closes_c  # alias

            # 計算指標
            ma5  = _calc_ma(c, 5)
            ma10 = _calc_ma(c, 10)
            ma20 = _calc_ma(c, 20)
            ma60 = _calc_ma(c, 60)
            rsi14 = _calc_rsi14(c)
            atr14 = _calc_atr14(highs, lows, closes_c)
            macd_val, macd_sig, macd_hist = _calc_macd(c)

            # 成交量均線
            vol_ma5  = _calc_ma(volumes, 5)
            vol_ma20 = _calc_ma(volumes, 20)

            # 報酬率
            ret_1d  = round((c[-1]/c[-2] - 1)*100, 4) if len(c) >= 2 else None
            ret_5d  = round((c[-1]/c[-6] - 1)*100, 4) if len(c) >= 6 else None
            ret_20d = round((c[-1]/c[-21] - 1)*100, 4) if len(c) >= 21 else None

            # 距 MA20
            dist_ma20 = round((c[-1]/ma20 - 1)*100, 4) if ma20 else None

            # 20日最高最低
            high_20d = max(highs[-20:]) if len(highs) >= 20 else None
            low_20d  = min(lows[-20:]) if len(lows) >= 20 else None

            # 20日波動率
            if len(c) >= 20:
                rets = [(c[i]/c[i-1]-1) for i in range(max(1,len(c)-20), len(c))]
                avg = sum(rets)/len(rets)
                vol20 = round(math.sqrt(sum((r-avg)**2 for r in rets)/len(rets))*100, 4)
            else:
                vol20 = None

            db.execute(text("""
                INSERT INTO technical_daily_features
                    (code, trade_date, ma5, ma10, ma20, ma60,
                     rsi14, macd, macd_signal, macd_hist,
                     volume_ma5, volume_ma20,
                     return_1d, return_5d, return_20d,
                     atr14, distance_ma20, high_20d, low_20d, volatility_20d,
                     updated_at)
                VALUES
                    (:code,:td,:ma5,:ma10,:ma20,:ma60,
                     :rsi,:macd,:ms,:mh,
                     :vm5,:vm20,
                     :r1,:r5,:r20,
                     :atr,:dma,:h20,:l20,:v20,
                     :now)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                    ma5=excluded.ma5, ma10=excluded.ma10,
                    ma20=excluded.ma20, ma60=excluded.ma60,
                    rsi14=excluded.rsi14, macd=excluded.macd,
                    volume_ma5=excluded.volume_ma5, volume_ma20=excluded.volume_ma20,
                    return_1d=excluded.return_1d, return_5d=excluded.return_5d,
                    return_20d=excluded.return_20d, atr14=excluded.atr14,
                    distance_ma20=excluded.distance_ma20,
                    high_20d=excluded.high_20d, low_20d=excluded.low_20d,
                    volatility_20d=excluded.volatility_20d,
                    updated_at=excluded.updated_at
            """), {
                "code": code, "td": str(target_date),
                "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                "rsi": rsi14, "macd": macd_val, "ms": macd_sig, "mh": macd_hist,
                "vm5": vol_ma5, "vm20": vol_ma20,
                "r1": ret_1d, "r5": ret_5d, "r20": ret_20d,
                "atr": atr14, "dma": dist_ma20,
                "h20": high_20d, "l20": low_20d, "v20": vol20,
                "now": now_str,
            })
            updated += 1

            if updated % 200 == 0:
                db.commit()
                logger.info(f"[TECH] {target_date} 進度 {updated}...")

        db.commit()
        logger.success(f"[TECH] {target_date} 計算完成 {updated} 檔")
        return updated

    except Exception as e:
        logger.error(f"[TECH] 失敗: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def get_technical_features(code: str, trade_date: str = None) -> dict | None:
    db = SessionLocal()
    try:
        q = "SELECT * FROM technical_daily_features WHERE code=:c"
        params = {"c": code}
        if trade_date:
            q += " AND trade_date=:d"
            params["d"] = trade_date
        else:
            q += " ORDER BY trade_date DESC LIMIT 1"
        row = db.execute(text(q), params).fetchone()
        if not row: return None
        cols = ["id","code","trade_date","ma5","ma10","ma20","ma60",
                "rsi14","macd","macd_signal","macd_hist",
                "volume_ma5","volume_ma20",
                "return_1d","return_5d","return_20d",
                "atr14","distance_ma20","high_20d","low_20d","volatility_20d",
                "created_at","updated_at"]
        return dict(zip(cols, row))
    finally:
        db.close()


def get_coverage_stats(trade_date: str = None) -> dict:
    """技術指標覆蓋率統計"""
    db = SessionLocal()
    try:
        if not trade_date:
            trade_date = db.execute(text(
                "SELECT MAX(trade_date) FROM technical_daily_features"
            )).scalar()

        total_ohlcv = db.execute(text(
            "SELECT COUNT(DISTINCT code) FROM ohlcv_daily WHERE trade_date=:d"
        ), {"d": trade_date}).scalar() or 0

        total_tech = db.execute(text(
            "SELECT COUNT(*) FROM technical_daily_features WHERE trade_date=:d"
        ), {"d": trade_date}).scalar() or 0

        missing_ma20 = db.execute(text(
            "SELECT COUNT(*) FROM technical_daily_features WHERE trade_date=:d AND ma20 IS NULL"
        ), {"d": trade_date}).scalar() or 0

        missing_rsi = db.execute(text(
            "SELECT COUNT(*) FROM technical_daily_features WHERE trade_date=:d AND rsi14 IS NULL"
        ), {"d": trade_date}).scalar() or 0

        return {
            "trade_date": trade_date,
            "total_ohlcv": total_ohlcv,
            "total_tech": total_tech,
            "coverage_pct": round(total_tech/total_ohlcv*100, 1) if total_ohlcv else 0,
            "missing_ma20": missing_ma20,
            "missing_rsi": missing_rsi,
        }
    finally:
        db.close()
