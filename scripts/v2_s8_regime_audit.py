from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import csv
import math

from sqlalchemy import text
from backend.models.database import SessionLocal


BENCHMARK_CODE = "0050"


def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def load_ohlcv(db):
    rows = db.execute(text("""
        SELECT code, trade_date, open, high, low, close, volume
        FROM ohlcv_daily
        WHERE open IS NOT NULL
          AND high IS NOT NULL
          AND low IS NOT NULL
          AND close IS NOT NULL
          AND volume IS NOT NULL
          AND volume > 0
        ORDER BY trade_date, code
    """)).fetchall()

    by_date = defaultdict(dict)
    dates = set()

    for r in rows:
        code = str(r[0])
        d = str(r[1])
        dates.add(d)
        by_date[d][code] = {
            "open": safe_float(r[2]),
            "high": safe_float(r[3]),
            "low": safe_float(r[4]),
            "close": safe_float(r[5]),
            "volume": safe_float(r[6]),
        }

    return sorted(dates), dict(by_date)


def build_benchmark_features(dates, by_date):
    close20 = deque(maxlen=20)
    close60 = deque(maxlen=60)

    out = {}

    prev_close = None

    for d in dates:
        p = by_date.get(d, {}).get(BENCHMARK_CODE)
        if not p:
            continue

        close = p["close"]

        close20.append(close)
        close60.append(close)

        ma20 = sum(close20) / len(close20) if len(close20) >= 20 else None
        ma60 = sum(close60) / len(close60) if len(close60) >= 60 else None

        ret1 = close / prev_close - 1.0 if prev_close and prev_close > 0 else 0.0

        out[d] = {
            "close": close,
            "ma20": ma20,
            "ma60": ma60,
            "ret1": ret1,
        }

        prev_close = close

    return out


def build_prev_close_map(dates, by_date):
    """
    建立每個交易日、每檔股票的昨日收盤價。
    用來計算真正的大盤廣度：今日 close vs 昨日 close。
    """
    last_close = {}
    prev_close_by_date = {}

    for d in dates:
        prev_close_by_date[d] = {}
        price_map = by_date.get(d, {})

        for code, p in price_map.items():
            if code in last_close:
                prev_close_by_date[d][code] = last_close[code]

        for code, p in price_map.items():
            close = safe_float(p.get("close"))
            if close > 0:
                last_close[code] = close

    return prev_close_by_date


def calc_market_breadth(trade_date, price_map, prev_close_by_date):
    """
    大盤廣度：
    正確版 = 今日收盤價 vs 昨日收盤價
    不是 close vs open。
    """
    up = 0
    down = 0

    prev_map = prev_close_by_date.get(trade_date, {})

    for code, p in price_map.items():
        if code == BENCHMARK_CODE:
            continue

        prev_close = prev_map.get(code)
        close = p["close"]

        if not prev_close or prev_close <= 0:
            continue

        if close > prev_close:
            up += 1
        elif close < prev_close:
            down += 1

    total = up + down
    breadth = up / total if total > 0 else 0.5

    return up, down, breadth


def classify_regime(bm, breadth):
    """
    第一版只用價格與市場廣度，之後再加新聞 / 財報 / 夜盤 / 美股。
    """

    if not bm:
        return "NEUTRAL", 50, "missing_0050"

    close = bm["close"]
    ma20 = bm["ma20"]
    ma60 = bm["ma60"]
    ret1 = bm["ret1"]

    score = 50
    reasons = []

    if ma20 and close > ma20:
        score += 15
        reasons.append("0050_above_ma20")
    elif ma20 and close < ma20:
        score -= 15
        reasons.append("0050_below_ma20")

    if ma60 and close > ma60:
        score += 15
        reasons.append("0050_above_ma60")
    elif ma60 and close < ma60:
        score -= 20
        reasons.append("0050_below_ma60")

    if breadth >= 0.58:
        score += 15
        reasons.append("breadth_strong")
    elif breadth <= 0.42:
        score -= 15
        reasons.append("breadth_weak")

    if ret1 >= 0.015:
        score += 8
        reasons.append("0050_strong_1d")
    elif ret1 <= -0.015:
        score -= 10
        reasons.append("0050_weak_1d")

    score = max(0, min(100, score))

    if score >= 70:
        mode = "OFFENSIVE"
    elif score <= 40:
        mode = "DEFENSIVE"
    else:
        mode = "NEUTRAL"

    return mode, score, ",".join(reasons)


def main():
    out_dir = Path("data/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    dates, by_date = load_ohlcv(db)
    db.close()

    bm_features = build_benchmark_features(dates, by_date)
    prev_close_by_date = build_prev_close_map(dates, by_date)

    rows = []

    for d in dates:
        price_map = by_date.get(d, {})
        up, down, breadth = calc_market_breadth(d, price_map, prev_close_by_date)
        bm = bm_features.get(d)

        mode, score, reason = classify_regime(bm, breadth)

        rows.append({
            "date": d,
            "regime_mode": mode,
            "regime_score": round(score, 2),
            "0050_close": round(bm["close"], 4) if bm else None,
            "0050_ma20": round(bm["ma20"], 4) if bm and bm["ma20"] else None,
            "0050_ma60": round(bm["ma60"], 4) if bm and bm["ma60"] else None,
            "0050_ret1_pct": round(bm["ret1"] * 100, 2) if bm else None,
            "up_count": up,
            "down_count": down,
            "breadth": round(breadth, 4),
            "reason": reason,
        })

    path = out_dir / "s8_regime_audit.csv"

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date",
            "regime_mode",
            "regime_score",
            "0050_close",
            "0050_ma20",
            "0050_ma60",
            "0050_ret1_pct",
            "up_count",
            "down_count",
            "breadth",
            "reason",
        ])
        writer.writeheader()
        writer.writerows(rows)

    counts = defaultdict(int)
    for r in rows:
        counts[r["regime_mode"]] += 1

    print("S8 regime audit finished.")
    print(f"Report: {path}")
    print("Mode counts:")
    for k in ["OFFENSIVE", "NEUTRAL", "DEFENSIVE"]:
        print(f"- {k}: {counts[k]}")

    print("\nLatest 20:")
    for r in rows[-20:]:
        print(
            r["date"],
            r["regime_mode"],
            r["regime_score"],
            "breadth=", r["breadth"],
            "reason=", r["reason"],
        )


if __name__ == "__main__":
    main()
