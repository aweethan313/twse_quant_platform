"""scripts/v7_market_timing.py
大盤擇時模型：根據加權指數位置決定部位乘數
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal


RULES = [
    # (條件描述, risk_level, position_multiplier)
    ("跌破MA60且量增", "high",   0.0),
    ("跌破MA20且跌破MA60", "high", 0.0),
    ("跌破MA20", "medium_high", 0.5),
    ("近MA20（-2%~0%）", "medium", 0.8),
    ("站上MA20且站上MA60", "low", 1.0),
    ("站上MA20", "low", 1.0),
]


def update_market_timing(target_date: date = None):
    if not target_date:
        target_date = date.today()

    db = SessionLocal()
    try:
        # 取加權指數（用台股市值加權，proxy 用 0050 或 TWII）
        # 優先從 market_context_daily 取
        mkt = None  # use 0050 fallback

        if not mkt:
            # fallback: 用 0050 作為大盤代理
            ohlcv = db.execute(text("""
                SELECT close FROM ohlcv_daily
                WHERE code='0050' AND trade_date=:d
            """), {"d": str(target_date)}).fetchone()
            if not ohlcv:
                print(f"[TIMING] {target_date} 無大盤資料")
                return None

            close_idx = float(ohlcv[0])
            # 計算 MA20/MA60
            hist = db.execute(text("""
                SELECT close FROM ohlcv_daily
                WHERE code='0050' AND trade_date <= :d
                ORDER BY trade_date DESC LIMIT 62
            """), {"d": str(target_date)}).fetchall()
            closes = [float(r[0]) for r in hist]
            ma20 = sum(closes[:20]) / 20 if len(closes) >= 20 else close_idx
            ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else close_idx
            breadth = None
        else:
            close_idx = float(mkt[0] or 0)
            ma20 = float(mkt[1] or close_idx)
            ma60 = float(mkt[2] or close_idx)
            breadth = mkt[3]

        above_ma20 = close_idx > ma20
        above_ma60 = close_idx > ma60
        dist_ma20  = (close_idx / ma20 - 1) * 100 if ma20 else 0

        # 決定風險等級
        if not above_ma20 and not above_ma60:
            risk = "high"; multiplier = 0.0; reason = "跌破MA20且MA60，暫停買進"
        elif not above_ma20:
            risk = "medium_high"; multiplier = 0.5; reason = f"跌破MA20（距離{dist_ma20:.1f}%），部位減半"
        elif above_ma20 and above_ma60 and dist_ma20 > 5:
            risk = "low"; multiplier = 1.0; reason = f"站穩MA20+MA60，可積極買進（+{dist_ma20:.1f}%）"
        elif above_ma20 and above_ma60:
            risk = "low"; multiplier = 1.0; reason = f"站穩MA20+MA60（+{dist_ma20:.1f}%）"
        else:
            risk = "medium"; multiplier = 0.8; reason = f"站上MA20但MA60未確認（{dist_ma20:.1f}%）"

        # 廣度修正
        if breadth and float(breadth) < 30:
            multiplier = min(multiplier, 0.5)
            reason += "；廣度分數低（市場廣度不佳）"
            risk = "medium_high"

        db.execute(text("""
            INSERT INTO market_timing_signals
                (trade_date, close, ma20, ma60, above_ma20, above_ma60,
                 risk_level, position_multiplier, breadth_score, reason_summary)
            VALUES (:d,:c,:m20,:m60,:am20,:am60,:risk,:mult,:bs,:reason)
            ON CONFLICT(trade_date) DO UPDATE SET
                close=excluded.close, ma20=excluded.ma20, ma60=excluded.ma60,
                risk_level=excluded.risk_level,
                position_multiplier=excluded.position_multiplier,
                reason_summary=excluded.reason_summary
        """), {"d": str(target_date), "c": close_idx, "m20": ma20, "m60": ma60,
               "am20": int(above_ma20), "am60": int(above_ma60),
               "risk": risk, "mult": multiplier, "bs": breadth, "reason": reason})
        db.commit()

        icon = "🟢" if risk=="low" else "🟡" if risk=="medium" else "🟠" if risk=="medium_high" else "🔴"
        print(f"[TIMING] {target_date} {icon} {risk} x{multiplier} | {reason}")
        return {"date": str(target_date), "risk": risk, "multiplier": multiplier, "reason": reason}

    finally:
        db.close()


def get_today_multiplier() -> float:
    """取今日部位乘數（供決策引擎使用）"""
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT position_multiplier FROM market_timing_signals
            ORDER BY trade_date DESC LIMIT 1
        """)).scalar()
        return float(r or 1.0)
    finally:
        db.close()


def rebuild_history(start_date="2025-01-01"):
    """重建歷史擇時訊號"""
    db = SessionLocal()
    try:
        dates = db.execute(text("""
            SELECT DISTINCT trade_date FROM trading_calendar
            WHERE is_open=1 AND trade_date >= :s
            ORDER BY trade_date
        """), {"s": start_date}).fetchall()
        db.close()
        print(f"重建 {len(dates)} 天擇時訊號...")
        for i, (d,) in enumerate(dates):
            update_market_timing(date.fromisoformat(str(d)))
            if (i+1) % 50 == 0:
                print(f"  進度 {i+1}/{len(dates)}")
        print("✓ 完成")
    except Exception as e:
        print(f"❌ {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--rebuild":
        rebuild_history(sys.argv[2] if len(sys.argv) > 2 else "2025-01-01")
    else:
        update_market_timing()
