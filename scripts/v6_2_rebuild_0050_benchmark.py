"""scripts/v6_2_rebuild_0050_benchmark.py
清理並重建 0050 benchmark，避免異常價格污染策略比較
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from pathlib import Path
from config.settings import settings

DB = str(settings.DB_PATH)
INIT_CASH = 200000.0
BENCHMARK_CODE = "0050"
MAX_DAILY_RETURN = 0.15  # 單日超過15%視為異常


def rebuild():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 確保欄位存在
    cols = [r[1] for r in cur.execute("PRAGMA table_info(benchmark_daily_equity)").fetchall()]
    if "is_valid" not in cols:
        cur.execute("ALTER TABLE benchmark_daily_equity ADD COLUMN is_valid INTEGER DEFAULT 1")
    if "anomaly_reason" not in cols:
        cur.execute("ALTER TABLE benchmark_daily_equity ADD COLUMN anomaly_reason TEXT")

    # 取 0050 資料（只用有效交易日）
    rows = cur.execute("""
        SELECT o.trade_date, o.open, o.close, o.volume
        FROM ohlcv_daily o
        JOIN trading_calendar tc ON tc.trade_date=o.trade_date
        WHERE o.code=? AND tc.is_open=1
        ORDER BY o.trade_date
    """, (BENCHMARK_CODE,)).fetchall()

    if not rows:
        # fallback：不用 trading_calendar
        rows = cur.execute("""
            SELECT trade_date, open, close, volume FROM ohlcv_daily
            WHERE code=? ORDER BY trade_date
        """, (BENCHMARK_CODE,)).fetchall()

    print(f"[BENCH] 0050 資料：{len(rows)} 筆")

    # 清理舊資料
    cur.execute("DELETE FROM benchmark_daily_equity WHERE benchmark_code=?", (BENCHMARK_CODE,))

    anomalies = []
    prev_close = None
    shares = None
    cash = INIT_CASH

    records = []
    for trade_date, open_p, close_p, volume in rows:
        close_f = float(close_p or 0)
        open_f = float(open_p or close_f)

        is_valid = 1
        anomaly_reason = None

        if close_f <= 0:
            is_valid = 0
            anomaly_reason = "close<=0"
            anomalies.append({"date": trade_date, "close": close_f, "reason": anomaly_reason})
        elif prev_close and prev_close > 0:
            daily_ret = (close_f - prev_close) / prev_close
            if abs(daily_ret) > MAX_DAILY_RETURN:
                is_valid = 0
                anomaly_reason = f"daily_return={daily_ret*100:.1f}%_exceeds_{MAX_DAILY_RETURN*100:.0f}%"
                anomalies.append({"date": trade_date, "close": close_f,
                                   "prev": prev_close, "ret": daily_ret*100,
                                   "reason": anomaly_reason})

        # 初始買入
        if shares is None and is_valid:
            buy_price = open_f if open_f > 0 else close_f
            if buy_price > 0:
                shares = INIT_CASH / buy_price
                cash = 0

        # 計算 equity
        if shares and is_valid and close_f > 0:
            equity = shares * close_f
        elif shares:
            equity = shares * (prev_close or close_f)
        else:
            equity = INIT_CASH

        daily_return = (close_f / prev_close - 1) * 100 if prev_close and prev_close > 0 and is_valid else 0
        cumulative_return = (equity / INIT_CASH - 1) * 100

        records.append((BENCHMARK_CODE, trade_date, close_f, round(daily_return, 4),
                        shares or 0, 0, round(equity, 2), cumulative_return,
                        is_valid, anomaly_reason))

        if is_valid:
            prev_close = close_f

    # 批次寫入
    # 移除 cash 欄位（表不支援）
    clean = [(r[0],r[1],r[2],r[3],r[4],r[6],r[7],r[8],r[9]) for r in records]
    cur.executemany("""
        INSERT INTO benchmark_daily_equity
            (benchmark_code, snap_date, price, daily_return, shares,
             equity, cumulative_return, is_valid, anomaly_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(benchmark_code, snap_date) DO UPDATE SET
            price=excluded.price, daily_return=excluded.daily_return,
            equity=excluded.equity, cumulative_return=excluded.cumulative_return,
            is_valid=excluded.is_valid, anomaly_reason=excluded.anomaly_reason
    """, clean)
    conn.commit()

    # 統計
    valid_cnt = sum(1 for r in records if r[8] == 1)
    final_equity = records[-1][6] if records else INIT_CASH
    total_return = (final_equity / INIT_CASH - 1) * 100
    start_date = records[0][1] if records else "N/A"
    end_date = records[-1][1] if records else "N/A"

    # 寫 anomalies CSV
    if anomalies:
        csv_path = Path("data/reports/v6_2_0050_benchmark_anomalies.csv")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date","close","prev","ret","reason"])
            w.writeheader()
            for a in anomalies:
                w.writerow({k: a.get(k, "") for k in ["date","close","prev","ret","reason"]})
        print(f"  異常資料：{csv_path}")

    # 寫 report
    report = f"""# V6-2 0050 Benchmark 重建報告

## 摘要
- benchmark_code：{BENCHMARK_CODE}
- 起始日：{start_date}
- 結束日：{end_date}
- 起始資產：NT${INIT_CASH:,.0f}
- 結束資產：NT${final_equity:,.2f}
- 總報酬率：{total_return:+.2f}%
- 有效資料筆數：{valid_cnt} / {len(records)}
- 異常資料筆數：{len(anomalies)}
- 異常門檻：單日報酬 > ±{MAX_DAILY_RETURN*100:.0f}%

## 異常資料
"""
    for a in anomalies[:10]:
        report += f"- {a['date']}: {a.get('reason','')}\n"
    if len(anomalies) > 10:
        report += f"... 共 {len(anomalies)} 筆，詳見 CSV\n"

    report += f"""
## 結論
- 異常資料已標記 is_valid=0，不參與 benchmark 計算
- 策略 vs 0050 比較使用清理後資料
"""
    rpath = Path("data/reports/v6_2_0050_benchmark_report.md")
    rpath.write_text(report, encoding="utf-8")

    print(f"\n=== 0050 Benchmark 重建完成 ===")
    print(f"  有效：{valid_cnt}/{len(records)} 筆")
    print(f"  異常：{len(anomalies)} 筆")
    print(f"  總報酬：{total_return:+.2f}%")
    print(f"  報告：{rpath}")
    conn.close()
    return {"total_return": total_return, "anomalies": len(anomalies), "valid": valid_cnt}

if __name__ == "__main__":
    rebuild()
