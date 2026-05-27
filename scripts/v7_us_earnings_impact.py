"""scripts/v7_us_earnings_impact.py - 美股財報季 / 重大事件對台股影響"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal

# 追蹤標的
TRACK_TICKERS = {
    "NVDA":  {"name": "NVIDIA", "tw_supply": ["2330","2454","6669","3711"]},
    "TSM":   {"name": "台積電ADR", "tw_supply": ["2330"]},
    "SOXX":  {"name": "費城半導體ETF", "tw_supply": ["2330","2454","2303","3711"]},
    "QQQ":   {"name": "NASDAQ100", "tw_supply": []},
    "^GSPC": {"name": "S&P500", "tw_supply": []},
    "MU":    {"name": "美光", "tw_supply": ["2303","3260","4763"]},
    "AMD":   {"name": "AMD", "tw_supply": ["2330","6770"]},
}

IMPACT_THRESHOLD = 5.0  # 美股單日漲跌 > 5% 才算重大事件


def detect_us_events(db, lookback_days: int = 30):
    """偵測美股重大事件"""
    from_date = str(date.today() - timedelta(days=lookback_days))

    # 從 overnight_market_data 取美股資料
    try:
        rows = db.execute(text("""
            SELECT ticker, trade_date, change_pct, close
            FROM overnight_market_data
            WHERE trade_date >= :d AND ABS(change_pct) >= :thresh
            ORDER BY trade_date DESC
        """), {"d": from_date, "thresh": IMPACT_THRESHOLD}).fetchall()
    except Exception:
        # overnight 表可能欄位不同
        try:
            rows = db.execute(text("""
                SELECT symbol, date, pct_change, price
                FROM overnight_market_data
                WHERE date >= :d AND ABS(pct_change) >= :thresh
                ORDER BY date DESC
            """), {"d": from_date, "thresh": IMPACT_THRESHOLD}).fetchall()
        except Exception as e:
            print(f"⚠️ 無法讀取 overnight_market_data: {e}")
            rows = []

    events = []
    for ticker, edate, chg, close in rows:
        if ticker not in TRACK_TICKERS:
            continue

        info = TRACK_TICKERS[ticker]
        chg_f = float(chg or 0)
        event_type = "EARNINGS_SURGE" if chg_f > IMPACT_THRESHOLD else \
                     "EARNINGS_CRASH" if chg_f < -IMPACT_THRESHOLD else "MOVE"

        # 計算台灣供應鏈的後續5日報酬
        tw_impact = []
        for tw_code in info["tw_supply"][:3]:
            r5 = db.execute(text("""
                SELECT AVG(tdf.return_5d) FROM technical_daily_features tdf
                WHERE tdf.code=:c AND tdf.trade_date > :d
                ORDER BY tdf.trade_date LIMIT 3
            """), {"c": tw_code, "d": str(edate)}).scalar()
            if r5: tw_impact.append(float(r5))

        avg_tw = sum(tw_impact)/len(tw_impact) if tw_impact else None

        try:
            db.execute(text("""
                INSERT OR IGNORE INTO us_market_events
                    (event_date, ticker, event_type, change_pct, tw_semi_impact_5d, note)
                VALUES (:d,:t,:et,:chg,:tw,:note)
            """), {"d": str(edate), "t": ticker, "et": event_type,
                   "chg": chg_f, "tw": avg_tw,
                   "note": f"{info['name']} {chg_f:+.1f}%"})
            events.append({"ticker": ticker, "date": str(edate), "change": chg_f, "tw_impact": avg_tw})
        except Exception:
            pass

    db.commit()
    return events


def print_report(db):
    """印出美股事件報告"""
    rows = db.execute(text("""
        SELECT event_date, ticker, event_type, change_pct, tw_semi_impact_5d, note
        FROM us_market_events ORDER BY event_date DESC LIMIT 10
    """)).fetchall()

    if not rows:
        print("⚠️ 暫無美股重大事件資料")
        return

    print("\n=== 美股重大事件 & 台股影響 ===")
    for edate, ticker, etype, chg, tw5, note in rows:
        tw_str = f"台半5日={float(tw5):+.1f}%" if tw5 else "台股影響待觀察"
        print(f"  {edate} {ticker:5} {float(chg):+6.1f}% [{etype}] {tw_str}")


def run():
    db = SessionLocal()
    try:
        events = detect_us_events(db)
        print(f"[US EVENTS] 偵測到 {len(events)} 個重大事件")
        print_report(db)
    finally:
        db.close()

if __name__ == "__main__":
    run()
