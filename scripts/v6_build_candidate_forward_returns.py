"""scripts/v6_build_candidate_forward_returns.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal


def get_score_bucket(score: float) -> str:
    if score >= 90: return "90+"
    if score >= 80: return "80~90"
    if score >= 70: return "70~80"
    if score >= 60: return "60~70"
    return "<60"


def build_forward_returns(start_date="2025-01-01", end_date=None):
    db = SessionLocal()
    try:
        if not end_date:
            end_date = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()

        print(f"建立候選股前瞻報酬 {start_date} ~ {end_date}...")

        # 取所有歷史候選股（BUY/WATCH 且有分數）
        candidates = db.execute(text("""
            SELECT DISTINCT ds.score_date, ds.code, sm.name,
                   ds.final_score, ds.final_score as rank_val,
                   o.close
            FROM daily_scores ds
            LEFT JOIN stock_meta sm ON sm.code=ds.code
            LEFT JOIN ohlcv_daily o ON o.code=ds.code AND o.trade_date=ds.score_date
            WHERE ds.score_date >= :s AND ds.score_date <= :e
              AND ds.final_action IN ('BUY','WATCH')
              AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','NORMAL')
              AND o.close IS NOT NULL AND o.close >= 10
            ORDER BY ds.score_date, ds.final_score DESC
        """), {"s": start_date, "e": str(end_date)}).fetchall()

        print(f"  候選股記錄：{len(candidates):,} 筆")

        # 取 0050 benchmark
        bench_map = {}
        bench_rows = db.execute(text("""
            SELECT snap_date, price FROM benchmark_daily_equity
            WHERE benchmark_code='0050'
        """)).fetchall()
        for d, p in bench_rows:
            bench_map[str(d)] = float(p or 0)

        inserted = 0
        for i, (sig_date, code, name, score, rank, close) in enumerate(candidates):
            if not close: continue
            close = float(close)

            # 取後續N日收盤
            future = db.execute(text("""
                SELECT trade_date, close FROM ohlcv_daily
                WHERE code=:c AND trade_date > :d
                ORDER BY trade_date LIMIT 22
            """), {"c": code, "d": str(sig_date)}).fetchall()

            def get_ret(n):
                if len(future) >= n:
                    fp = float(future[n-1][1] or close)
                    return round((fp/close - 1)*100, 3)
                return None

            # 0050 benchmark 報酬
            bench_base = bench_map.get(str(sig_date))
            def bench_ret(n):
                if len(future) >= n and bench_base:
                    fd = str(future[n-1][0])
                    fb = bench_map.get(fd)
                    if fb and bench_base:
                        return round((fb/bench_base - 1)*100, 3)
                return None

            r1 = get_ret(1); r3 = get_ret(3); r5 = get_ret(5)
            r10 = get_ret(10); r20 = get_ret(20)
            b1 = bench_ret(1); b5 = bench_ret(5); b10 = bench_ret(10); b20 = bench_ret(20)

            # 最大上漲/下跌（20日內）
            closes_20 = [float(f[1]) for f in future[:20] if f[1]]
            max_up = round((max(closes_20)/close - 1)*100, 3) if closes_20 else None
            max_dd = round((min(closes_20)/close - 1)*100, 3) if closes_20 else None

            db.execute(text("""
                INSERT INTO candidate_forward_returns
                    (signal_date, code, stock_name, candidate_score, score_bucket,
                     rank, close_price,
                     return_1d, return_3d, return_5d, return_10d, return_20d,
                     alpha_1d_vs_0050, alpha_5d_vs_0050, alpha_10d_vs_0050, alpha_20d_vs_0050,
                     max_runup_20d, max_drawdown_20d)
                VALUES (:sd,:c,:n,:sc,:sb,:rk,:cl,
                        :r1,:r3,:r5,:r10,:r20,
                        :a1,:a5,:a10,:a20,:mu,:md)
                ON CONFLICT(signal_date,code) DO UPDATE SET
                    return_1d=excluded.return_1d, return_5d=excluded.return_5d,
                    return_10d=excluded.return_10d, return_20d=excluded.return_20d
            """), {
                "sd": str(sig_date), "c": code, "n": name,
                "sc": score, "sb": get_score_bucket(float(score or 0)),
                "rk": rank, "cl": close,
                "r1": r1, "r3": r3, "r5": r5, "r10": r10, "r20": r20,
                "a1": (r1 - b1) if r1 and b1 else None,
                "a5": (r5 - b5) if r5 and b5 else None,
                "a10": (r10 - b10) if r10 and b10 else None,
                "a20": (r20 - b20) if r20 and b20 else None,
                "mu": max_up, "md": max_dd,
            })
            inserted += 1
            if inserted % 1000 == 0:
                db.commit()
                print(f"  進度 {inserted:,}/{len(candidates):,}...")

        db.commit()
        print(f"✓ 候選股前瞻報酬建立完成，{inserted:,} 筆")

        # 印出分數區間統計
        print("\n=== 分數區間勝率（5日）===")
        stats = db.execute(text("""
            SELECT score_bucket,
                   COUNT(*) as n,
                   AVG(return_5d) as avg_5d,
                   AVG(return_10d) as avg_10d,
                   AVG(alpha_5d_vs_0050) as avg_alpha5,
                   SUM(CASE WHEN return_5d > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*) as wr
            FROM candidate_forward_returns
            WHERE return_5d IS NOT NULL
            GROUP BY score_bucket ORDER BY score_bucket DESC
        """)).fetchall()
        print(f"  {'分數':8} {'樣本':6} {'均5日':8} {'均10日':8} {'alpha5':8} {'勝率':6}")
        for r in stats:
            print(f"  {r[0]:8} {r[1]:6} {(r[2] or 0):+7.2f}% {(r[3] or 0):+7.2f}% {(r[4] or 0):+7.2f}% {(r[5] or 0):5.1f}%")

    finally:
        db.close()

if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    e = sys.argv[2] if len(sys.argv) > 2 else None
    build_forward_returns(s, e)
