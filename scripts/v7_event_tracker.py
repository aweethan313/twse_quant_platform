"""scripts/v7_event_tracker.py - 財報/月營收事件追蹤"""
import sys, os, requests, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal
from loguru import logger


def fetch_monthly_revenue(year: int, month: int) -> list:
    """從 TWSE 抓月營收資料"""
    # TWSE 月營收：每月 10 日前後公布上月
    url = f"https://mops.twse.com.tw/nas/t21/sii/t21sc03_{year}_{month-1911}_0.html"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        # 簡單解析：找股票代號和營收數字
        import re
        rows = []
        # 找 4~6 碼股票代號
        codes = re.findall(r'<td[^>]*>(\d{4,6})</td>', r.text)
        if codes:
            logger.info(f"月營收 {year}/{month}：找到 {len(codes)} 筆")
        return codes[:20]  # 示意，實際需要完整解析
    except Exception as e:
        logger.warning(f"月營收抓取失敗 {year}/{month}: {e}")
        return []


def build_event_calendar(db, lookback_months: int = 3):
    """建立近期事件日曆（月營收）"""
    today = date.today()
    inserted = 0

    for i in range(lookback_months):
        target = today.replace(day=1) - timedelta(days=i*30)
        year, month = target.year, target.month

        # 月營收通常在每月 10 日前後公布
        announce_date = date(year, month, 10)
        if month == 12:
            revenue_month = date(year, month, 1)
        else:
            revenue_month = date(year, month - 1 if month > 1 else 12, 1)

        # 取重要大型股
        top_stocks = db.execute(text("""
            SELECT DISTINCT ds.code, sm.name FROM daily_scores ds
            LEFT JOIN stock_meta sm ON sm.code=ds.code
            WHERE ds.stock_class IN ('CORE_LARGE_CAP','LARGE_LIQUID')
            LIMIT 50
        """)).fetchall()

        for code, name in top_stocks:
            try:
                db.execute(text("""
                    INSERT OR IGNORE INTO stock_event_calendar
                        (code, stock_name, event_type, event_date, announcement_time,
                         source, title, is_confirmed)
                    VALUES (:c,:n,'monthly_revenue',:ed,:at,'TWSE',:title,0)
                """), {
                    "c": code, "n": name or code,
                    "ed": str(announce_date),
                    "at": f"{year}-{month:02d}-10 18:00",
                    "title": f"{year}年{month-1 if month>1 else 12}月營收"
                })
                inserted += 1
            except Exception:
                pass

    db.commit()
    return inserted


def analyze_event_returns(db):
    """分析事件後的報酬"""
    events = db.execute(text("""
        SELECT e.id, e.code, e.event_type, e.event_date
        FROM stock_event_calendar e
        WHERE NOT EXISTS (
            SELECT 1 FROM event_return_analysis er WHERE er.event_id=e.id
        )
        AND e.event_date <= :today
        LIMIT 100
    """), {"today": str(date.today())}).fetchall()

    analyzed = 0
    for eid, code, etype, edate in events:
        # 取事件前一日收盤
        before = db.execute(text("""
            SELECT close FROM ohlcv_daily
            WHERE code=:c AND trade_date < :d
            ORDER BY trade_date DESC LIMIT 1
        """), {"c": code, "d": edate}).scalar()

        if not before or float(before) <= 0:
            continue

        before_f = float(before)

        def get_ret(n):
            future = db.execute(text("""
                SELECT close FROM ohlcv_daily
                WHERE code=:c AND trade_date > :d
                ORDER BY trade_date LIMIT :n
            """), {"c": code, "d": edate, "n": n}).fetchall()
            if len(future) >= n:
                return round((float(future[-1][0]) / before_f - 1) * 100, 3)
            return None

        r1 = get_ret(1); r3 = get_ret(3); r5 = get_ret(5); r10 = get_ret(10)

        conclusion = "POSITIVE" if (r5 or 0) > 2 else "NEGATIVE" if (r5 or 0) < -2 else "NEUTRAL"

        db.execute(text("""
            INSERT OR IGNORE INTO event_return_analysis
                (event_id, code, event_type, event_date, close_before,
                 return_1d, return_3d, return_5d, return_10d, conclusion)
            VALUES (:eid,:c,:et,:ed,:cb,:r1,:r3,:r5,:r10,:con)
        """), {"eid": eid, "c": code, "et": etype, "ed": edate,
               "cb": before_f, "r1": r1, "r3": r3, "r5": r5, "r10": r10, "con": conclusion})
        analyzed += 1

    db.commit()
    return analyzed


def run():
    db = SessionLocal()
    try:
        n1 = build_event_calendar(db)
        n2 = analyze_event_returns(db)
        print(f"✓ 事件日曆建立：{n1} 筆")
        print(f"✓ 事件報酬分析：{n2} 筆")
    finally:
        db.close()

if __name__ == "__main__":
    run()
