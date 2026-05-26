"""
scripts/historical_accuracy_backtest.py
歷史選股準確率回測 2025-01-01 ~ 今天
模擬每天用當日收盤資料，選出符合條件的股票，對比隔日實際表現
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.models.database import SessionLocal
from sqlalchemy import text
from datetime import date
import json
from pathlib import Path

def run_backtest(start_date="2025-01-01", end_date=None):
    if end_date is None:
        end_date = str(date.today())

    db = SessionLocal()
    try:
        # 取所有交易日
        trade_dates = db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date >= :s AND trade_date < :e
            ORDER BY trade_date
        """), {"s": start_date, "e": end_date}).fetchall()

        print(f"回測期間：{start_date} ~ {end_date}，共 {len(trade_dates)} 個交易日")

        all_days = []
        total_picks = wins = 0

        for i, (signal_date,) in enumerate(trade_dates[:-1]):
            next_date = trade_dates[i+1][0]

            # 模擬選股（與現在相同條件）
            picks = db.execute(text("""
                SELECT DISTINCT ds.code, sm.name, ds.stock_class,
                       ds.final_score, ds.risk_score,
                       tdf.rsi14, tdf.distance_ma20, tdf.return_5d,
                       o_sig.close as ref_price,
                       o_rev.close as next_close,
                       o_rev.change_pct
                FROM daily_scores ds
                LEFT JOIN stock_meta sm ON sm.code=ds.code
                LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code
                    AND tdf.trade_date=:sig
                LEFT JOIN ohlcv_daily o_sig ON o_sig.code=ds.code
                    AND o_sig.trade_date=:sig
                LEFT JOIN ohlcv_daily o_rev ON o_rev.code=ds.code
                    AND o_rev.trade_date=:rev
                WHERE ds.score_date=:sig
                  AND ds.final_action IN ('BUY','WATCH')
                  AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','SPECULATIVE_HOT','NORMAL')
                  AND o_sig.close >= 10
                  AND o_rev.close IS NOT NULL
                  AND (tdf.rsi14 IS NULL OR (tdf.rsi14 >= 30 AND tdf.rsi14 < 80))
                  AND (tdf.distance_ma20 IS NULL OR ABS(tdf.distance_ma20) < 12)
                  AND (tdf.return_5d IS NULL OR tdf.return_5d < 12)
                ORDER BY
                    CASE ds.stock_class WHEN 'CORE_LARGE_CAP' THEN 1
                        WHEN 'LARGE_LIQUID' THEN 2 ELSE 3 END,
                    ds.final_score DESC
                LIMIT 10
            """), {"sig": str(signal_date), "rev": str(next_date)}).fetchall()

            if not picks:
                continue

            day_rets = [float(r[10] or 0) for r in picks]
            day_wins = sum(1 for r in day_rets if r > 0)
            day_avg  = sum(day_rets) / len(day_rets)

            all_days.append({
                "signal_date": str(signal_date),
                "next_date": str(next_date),
                "n": len(picks),
                "wins": day_wins,
                "win_rate": round(day_wins/len(picks)*100, 1),
                "avg_return": round(day_avg, 3),
            })
            total_picks += len(picks)
            wins += day_wins

            if (i+1) % 50 == 0:
                print(f"  進度 {i+1}/{len(trade_dates)-1}...")

        # 統計
        total_days = len(all_days)
        overall_wr = wins/total_picks*100 if total_picks else 0
        overall_avg = sum(d["avg_return"] for d in all_days)/total_days if total_days else 0
        positive_days = sum(1 for d in all_days if d["avg_return"] > 0)

        print(f"\n=== 歷史回測結果 {start_date}~{end_date} ===")
        print(f"回測天數：{total_days} 天")
        print(f"總選股次數：{total_picks} 次")
        print(f"整體勝率：{overall_wr:.1f}%")
        print(f"平均每日報酬：{overall_avg:+.3f}%")
        print(f"正報酬日：{positive_days}/{total_days}（{positive_days/total_days*100:.0f}%）")

        # 月度統計
        monthly = {}
        for d in all_days:
            ym = d["signal_date"][:7]
            if ym not in monthly:
                monthly[ym] = {"days":0,"wins":0,"total":0,"sum_ret":0}
            monthly[ym]["days"] += 1
            monthly[ym]["wins"] += d["wins"]
            monthly[ym]["total"] += d["n"]
            monthly[ym]["sum_ret"] += d["avg_return"]

        print(f"\n月度績效：")
        print(f"{'月份':8} {'天數':4} {'勝率':6} {'平均報酬':8}")
        for ym, m in sorted(monthly.items()):
            wr = m["wins"]/m["total"]*100 if m["total"] else 0
            avg = m["sum_ret"]/m["days"] if m["days"] else 0
            bar = "█" * int(wr/10)
            print(f"{ym}  {m['days']:3}天  {wr:5.1f}%  {avg:+.2f}%  {bar}")

        # 存結果
        result = {
            "start_date": start_date,
            "end_date": end_date,
            "total_days": total_days,
            "total_picks": total_picks,
            "overall_win_rate": round(overall_wr, 2),
            "avg_daily_return": round(overall_avg, 4),
            "positive_days_pct": round(positive_days/total_days*100, 1) if total_days else 0,
            "monthly": monthly,
            "daily": all_days,
        }
        path = Path(f"data/reports/historical_accuracy_{start_date}_{end_date}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n✓ 結果儲存：{path}")
        return result

    finally:
        db.close()

if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    end   = sys.argv[2] if len(sys.argv) > 2 else str(date.today())
    run_backtest(start, end)
