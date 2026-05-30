"""scripts/v8_expand_training_data.py
把歷史上所有候選池股票都跑前瞻報酬，
從 746 筆擴增到幾萬筆，大幅提升 ML 模型品質
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal
from loguru import logger


def expand_training_data(start_date="2025-01-01", end_date=None, limit_per_day=50):
    """
    從 daily_scores 取出所有候選股（不限於最終選入的），
    計算 5/10/20 日前瞻報酬，存入 candidate_forward_returns
    """
    if not end_date:
        end_date = str(date.today() - timedelta(days=25))  # 至少留 25 天給前瞻計算

    db = SessionLocal()
    try:
        # 取交易日列表
        trade_dates = [r[0] for r in db.execute(text("""
            SELECT DISTINCT trade_date FROM trading_calendar
            WHERE is_open=1 AND trade_date >= :s AND trade_date <= :e
            ORDER BY trade_date
        """), {"s": start_date, "e": end_date}).fetchall()]

        logger.info(f"[EXPAND] {start_date}~{end_date} 共 {len(trade_dates)} 個交易日")

        total_added = 0
        for i, signal_date in enumerate(trade_dates):
            # 取當天所有評分股票（分數>=50，不只選入的）
            candidates = db.execute(text("""
                SELECT ds.code, sm.name,
                       ds.final_score, ds.momentum_score, ds.chip_score,
                       ds.risk_score, ds.valuation_score, ds.core_score,
                       tdf.rsi14, tdf.distance_ma20, tdf.return_5d, tdf.return_1d,
                       COALESCE(cd.foreign_net,0), COALESCE(cd.trust_net,0)
                FROM daily_scores ds
                LEFT JOIN stock_meta sm ON sm.code=ds.code
                LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code AND tdf.trade_date=:d
                LEFT JOIN chip_daily cd ON cd.code=ds.code AND cd.trade_date=:d
                WHERE ds.score_date=:d
                  AND ds.final_score >= 50
                  AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK')
                ORDER BY ds.final_score DESC
                LIMIT :lim
            """), {"d": signal_date, "lim": limit_per_day}).fetchall()

            if not candidates:
                continue

            # 計算前瞻報酬：找 5/10/20 日後的收盤價
            day_added = 0
            for row in candidates:
                code = row[0]

                # 已存在則跳過
                existing = db.execute(text("""
                    SELECT id FROM candidate_forward_returns
                    WHERE code=:c AND signal_date=:d
                """), {"c": code, "d": signal_date}).scalar()
                if existing:
                    continue

                # 取訊號日收盤價
                base = db.execute(text("""
                    SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d
                """), {"c": code, "d": signal_date}).scalar()
                if not base:
                    continue

                # 取後續 5/10/20 日收盤
                future_dates = trade_dates[i+1:i+25] if i+25 < len(trade_dates) else trade_dates[i+1:]

                def get_nth_close(n):
                    dates_after = future_dates[:n+5]
                    for fd in dates_after:
                        cl = db.execute(text(
                            "SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d"
                        ), {"c": code, "d": fd}).scalar()
                        if cl:
                            return cl, fd
                    return None, None

                # 找第5、10、20個交易日
                def get_close_at_day(n):
                    if i+n >= len(trade_dates):
                        return None, None
                    target_dates = trade_dates[i+1:i+n+5]
                    valid_closes = []
                    for td in target_dates:
                        cl = db.execute(text(
                            "SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d"
                        ), {"c": code, "d": td}).scalar()
                        if cl:
                            valid_closes.append((cl, td))
                    if len(valid_closes) >= n:
                        return valid_closes[n-1]
                    elif valid_closes:
                        return valid_closes[-1]
                    return None, None

                cl5, d5   = get_close_at_day(5)
                cl10, d10 = get_close_at_day(10)
                cl20, d20 = get_close_at_day(20)

                r5  = (cl5/float(base)-1)*100  if cl5  else None
                r10 = (cl10/float(base)-1)*100 if cl10 else None
                r20 = (cl20/float(base)-1)*100 if cl20 else None

                db.execute(text("""
                    INSERT OR IGNORE INTO candidate_forward_returns
                        (code, stock_name, signal_date, return_5d, return_10d, return_20d,
                         final_score, momentum_score, chip_score, risk_score,
                         valuation_score, core_score)
                    VALUES (:c,:n,:sd,:r5,:r10,:r20,:fs,:ms,:cs,:rs,:vs,:crs)
                """), {
                    "c": code, "n": row[1], "sd": signal_date,
                    "r5": r5, "r10": r10, "r20": r20,
                    "fs": float(row[2] or 50), "ms": float(row[4] or 50),
                    "cs": float(row[5] or 50), "rs": float(row[6] or 50),
                    "vs": float(row[7] or 50), "crs": float(row[8] or 50),
                })
                day_added += 1

            db.commit()
            total_added += day_added

            if (i+1) % 20 == 0:
                logger.info(f"[EXPAND] 進度 {i+1}/{len(trade_dates)} | 新增 {total_added} 筆")

        logger.success(f"[EXPAND] 完成！新增 {total_added} 筆前瞻報酬")

        # 統計
        total = db.execute(text("SELECT COUNT(*) FROM candidate_forward_returns")).scalar()
        logger.info(f"[EXPAND] candidate_forward_returns 總計 {total} 筆")
        return total_added

    finally:
        db.close()


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    end   = sys.argv[2] if len(sys.argv) > 2 else None
    lim   = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    expand_training_data(start, end, lim)
