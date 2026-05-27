"""scripts/v6_3_daily_data_quality_audit.py
全市場日K資料品質審計
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from pathlib import Path
from collections import defaultdict
from config.settings import settings

DB = str(settings.DB_PATH)

def run_audit():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    issues = []
    stats = defaultdict(int)

    print("掃描 ohlcv_daily...")

    # 1. 重複資料
    dups = cur.execute("""
        SELECT code, trade_date, COUNT(*) as cnt
        FROM ohlcv_daily GROUP BY code, trade_date HAVING cnt > 1
    """).fetchall()
    for code, d, cnt in dups:
        issues.append({"severity":"CRITICAL","type":"DUPLICATE","code":code,
                        "date":d,"detail":f"重複 {cnt} 筆"})
        stats["CRITICAL"] += 1

    # 2. 非交易日資料
    non_td = cur.execute("""
        SELECT DISTINCT o.code, o.trade_date
        FROM ohlcv_daily o
        LEFT JOIN trading_calendar tc ON tc.trade_date=o.trade_date
        WHERE tc.is_open=0 OR tc.trade_date IS NULL
        LIMIT 100
    """).fetchall()
    for code, d in non_td:
        issues.append({"severity":"CRITICAL","type":"NON_TRADING_DAY","code":code,"date":d,"detail":"非交易日有資料"})
        stats["CRITICAL"] += 1

    # 3. close <= 0
    bad_close = cur.execute("""
        SELECT code, trade_date, close FROM ohlcv_daily
        WHERE close IS NULL OR close <= 0 LIMIT 100
    """).fetchall()
    for code, d, close in bad_close:
        issues.append({"severity":"CRITICAL","type":"INVALID_CLOSE","code":code,
                        "date":d,"detail":f"close={close}"})
        stats["CRITICAL"] += 1

    # 4. high < low
    bad_hl = cur.execute("""
        SELECT code, trade_date, high, low FROM ohlcv_daily
        WHERE high IS NOT NULL AND low IS NOT NULL AND high < low LIMIT 100
    """).fetchall()
    for code, d, h, l in bad_hl:
        issues.append({"severity":"CRITICAL","type":"HIGH_LESS_THAN_LOW","code":code,
                        "date":d,"detail":f"high={h} low={l}"})
        stats["CRITICAL"] += 1

    # 5. 單日報酬 > 20%
    big_ret = cur.execute("""
        SELECT a.code, a.trade_date, a.close, b.close as prev_close,
               (a.close - b.close) / b.close * 100 as ret
        FROM ohlcv_daily a
        JOIN ohlcv_daily b ON b.code=a.code
        JOIN (
            SELECT code, trade_date,
                   LAG(trade_date) OVER (PARTITION BY code ORDER BY trade_date) as prev_date
            FROM ohlcv_daily
        ) lag ON lag.code=a.code AND lag.trade_date=a.trade_date
        JOIN ohlcv_daily b2 ON b2.code=a.code AND b2.trade_date=lag.prev_date
        WHERE ABS((a.close - b2.close) / b2.close) > 0.20
          AND b2.close > 0
        LIMIT 50
    """).fetchall()
    for code, d, close, prev, ret in big_ret:
        issues.append({"severity":"WARNING","type":"LARGE_RETURN","code":code,
                        "date":d,"detail":f"報酬{ret:+.1f}% close={close} prev={prev}"})
        stats["WARNING"] += 1

    # 6. close 不在 high-low 範圍
    bad_range = cur.execute("""
        SELECT code, trade_date, open, high, low, close FROM ohlcv_daily
        WHERE high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
          AND (close > high * 1.001 OR close < low * 0.999)
        LIMIT 50
    """).fetchall()
    for code, d, o, h, l, c in bad_range:
        issues.append({"severity":"WARNING","type":"CLOSE_OUT_OF_RANGE","code":code,
                        "date":d,"detail":f"close={c} H={h} L={l}"})
        stats["WARNING"] += 1

    # 7. 缺 volume
    no_vol = cur.execute("""
        SELECT COUNT(*) FROM ohlcv_daily WHERE volume IS NULL OR volume < 0
    """).fetchone()[0]
    if no_vol:
        issues.append({"severity":"WARNING","type":"MISSING_VOLUME","code":"ALL",
                        "date":"","detail":f"{no_vol} 筆 volume 缺失"})
        stats["WARNING"] += 1

    # 統計
    total_rows = cur.execute("SELECT COUNT(*) FROM ohlcv_daily").fetchone()[0]
    total_stocks = cur.execute("SELECT COUNT(DISTINCT code) FROM ohlcv_daily").fetchone()[0]
    date_range = cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM ohlcv_daily").fetchone()

    conn.close()

    # 寫 CSV
    csv_path = Path("data/reports/v6_3_daily_data_quality_issues.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if issues:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["severity","type","code","date","detail"])
            w.writeheader()
            w.writerows(issues)

    # 寫 report
    report = f"""# V6-3 全市場日K資料品質審計

## 資料概況
- 總資料筆數：{total_rows:,}
- 股票總數：{total_stocks:,}
- 日期範圍：{date_range[0]} ~ {date_range[1]}

## 問題統計
- CRITICAL（嚴重）：{stats['CRITICAL']} 筆
- WARNING（警告）：{stats['WARNING']} 筆
- 總問題：{len(issues)} 筆

## CRITICAL 問題
"""
    for issue in [i for i in issues if i['severity']=='CRITICAL'][:20]:
        report += f"- [{issue['type']}] {issue['code']} {issue['date']}: {issue['detail']}\n"

    report += f"""
## WARNING 問題
"""
    for issue in [i for i in issues if i['severity']=='WARNING'][:20]:
        report += f"- [{issue['type']}] {issue['code']} {issue['date']}: {issue['detail']}\n"

    report += f"""
## 結論
- CRITICAL 資料不應用於 benchmark 和策略比較
- 詳細問題清單：data/reports/v6_3_daily_data_quality_issues.csv
"""
    rpath = Path("data/reports/v6_3_daily_data_quality_audit.md")
    rpath.write_text(report, encoding="utf-8")

    print(f"\n=== 資料品質審計完成 ===")
    print(f"  CRITICAL：{stats['CRITICAL']} | WARNING：{stats['WARNING']}")
    print(f"  報告：{rpath}")
    return {"critical": stats["CRITICAL"], "warning": stats["WARNING"]}

if __name__ == "__main__":
    run_audit()
