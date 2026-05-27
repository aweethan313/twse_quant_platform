"""scripts/v6_1_build_trading_calendar.py
建立 trading_calendar 並產生 audit report
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from pathlib import Path
from config.settings import settings

DB = str(settings.DB_PATH)

def build():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 建立 trading_calendar
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_open INTEGER DEFAULT 1,
            weekday INTEGER,
            source TEXT DEFAULT 'ohlcv_daily',
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 從 ohlcv_daily 取所有日期
    dates = cur.execute("""
        SELECT DISTINCT trade_date FROM ohlcv_daily
        ORDER BY trade_date
    """).fetchall()

    from datetime import date as ddate
    inserted = updated = 0
    non_trading = []

    for (d,) in dates:
        dt = ddate.fromisoformat(str(d))
        weekday = dt.weekday()  # 0=Mon, 6=Sun
        is_open = 1 if weekday < 5 else 0  # 週末關市
        note = "weekend" if weekday >= 5 else ""

        if weekday >= 5:
            non_trading.append(d)

        existing = cur.execute(
            "SELECT is_open FROM trading_calendar WHERE trade_date=?", (d,)
        ).fetchone()

        if not existing:
            cur.execute("""
                INSERT INTO trading_calendar (trade_date, is_open, weekday, source, note)
                VALUES (?, ?, ?, 'ohlcv_daily', ?)
            """, (d, is_open, weekday, note))
            inserted += 1
        else:
            updated += 1

    conn.commit()

    # 統計
    total = cur.execute("SELECT COUNT(*) FROM trading_calendar").fetchone()[0]
    open_days = cur.execute("SELECT COUNT(*) FROM trading_calendar WHERE is_open=1").fetchone()[0]
    closed_days = cur.execute("SELECT COUNT(*) FROM trading_calendar WHERE is_open=0").fetchone()[0]
    latest_open = cur.execute("SELECT MAX(trade_date) FROM trading_calendar WHERE is_open=1").fetchone()[0]
    ohlcv_range = cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM ohlcv_daily").fetchone()

    conn.close()

    # 產生 audit report
    report = f"""# V6-1 Trading Calendar Audit

## 摘要
- 總交易日曆筆數：{total}
- 有效交易日（is_open=1）：{open_days}
- 非交易日（is_open=0）：{closed_days}
- 最新有效交易日：{latest_open}
- ohlcv_daily 覆蓋範圍：{ohlcv_range[0]} ~ {ohlcv_range[1]}
- 新增：{inserted}，已存在：{updated}

## 疑似非交易日但有資料（週末）
共 {len(non_trading)} 筆：
"""
    for d in non_trading[:20]:
        report += f"- {d}\n"
    if len(non_trading) > 20:
        report += f"... 共 {len(non_trading)} 筆\n"

    report += f"""
## 建議
1. 回測查詢最新日期請改用：
   `SELECT MAX(trade_date) FROM trading_calendar WHERE is_open=1`
2. 非交易日資料（{len(non_trading)}筆週末）不應影響策略計算
3. 建議未來接入 TWSE 官方休市日資料補充假日
"""

    path = Path("data/reports/v6_1_trading_calendar_audit.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")

    print(f"✓ trading_calendar 建立完成")
    print(f"  有效交易日：{open_days} / 總日期：{total}")
    print(f"  最新交易日：{latest_open}")
    print(f"  非交易日（週末）：{len(non_trading)} 筆")
    print(f"  報告：{path}")
    return {"open_days": open_days, "latest": latest_open, "non_trading": len(non_trading)}

if __name__ == "__main__":
    build()
