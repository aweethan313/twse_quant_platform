from backend.models.database import SessionLocal
from backend.signals.scorer import compute_scores
from sqlalchemy import text
from datetime import date


def main():
    db = SessionLocal()

    latest_ohlcv = db.execute(text("""
        SELECT MAX(trade_date)
        FROM ohlcv_daily
    """)).scalar()

    print(f"latest_ohlcv = {latest_ohlcv}")

    # 1. 顯示 ohlcv_daily 同日同股票重複
    dup_ohlcv = db.execute(text("""
        SELECT code, COUNT(*) AS cnt
        FROM ohlcv_daily
        WHERE trade_date = :d
        GROUP BY code
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC, code
    """), {"d": latest_ohlcv}).fetchall()

    print("\n[1] duplicate OHLCV codes:")
    if not dup_ohlcv:
        print("  none")
    else:
        for r in dup_ohlcv[:50]:
            print(" ", r)

    # 2. 刪除 latest trade date 的 OHLCV 重複列，只保留 rowid 最大者
    db.execute(text("""
        DELETE FROM ohlcv_daily
        WHERE trade_date = :d
          AND rowid NOT IN (
              SELECT MAX(rowid)
              FROM ohlcv_daily
              WHERE trade_date = :d
              GROUP BY code, trade_date
          )
    """), {"d": latest_ohlcv})
    db.commit()

    # 3. 刪除 latest daily_scores，等等用最新 OHLCV 重新算
    db.execute(text("""
        DELETE FROM daily_scores
        WHERE score_date = :d
    """), {"d": latest_ohlcv})
    db.commit()

    # 4. 用 latest OHLCV 的 distinct code 重新算分數
    codes = [
        r[0] for r in db.execute(text("""
            SELECT DISTINCT code
            FROM ohlcv_daily
            WHERE trade_date = :d
              AND close IS NOT NULL
              AND close > 0
            ORDER BY code
        """), {"d": latest_ohlcv}).fetchall()
    ]

    db.close()

    print(f"\n[2] recompute latest scores: {latest_ohlcv}, codes={len(codes)}")
    compute_scores(codes, date.fromisoformat(str(latest_ohlcv)))

    # 5. 驗證
    db = SessionLocal()

    ohlcv_codes = db.execute(text("""
        SELECT COUNT(DISTINCT code)
        FROM ohlcv_daily
        WHERE trade_date = :d
          AND close IS NOT NULL
          AND close > 0
    """), {"d": latest_ohlcv}).scalar()

    score_codes = db.execute(text("""
        SELECT COUNT(DISTINCT code)
        FROM daily_scores
        WHERE score_date = :d
    """), {"d": latest_ohlcv}).scalar()

    score_rows = db.execute(text("""
        SELECT COUNT(*)
        FROM daily_scores
        WHERE score_date = :d
    """), {"d": latest_ohlcv}).scalar()

    score_only = db.execute(text("""
        SELECT COUNT(*)
        FROM daily_scores s
        LEFT JOIN ohlcv_daily o
          ON o.code = s.code
         AND o.trade_date = s.score_date
        WHERE s.score_date = :d
          AND o.code IS NULL
    """), {"d": latest_ohlcv}).scalar()

    print("\n[3] validation")
    print("ohlcv distinct codes:", ohlcv_codes)
    print("score distinct codes:", score_codes)
    print("score rows:", score_rows)
    print("score without OHLCV:", score_only)
    print("coverage:", round(score_codes / ohlcv_codes * 100, 2) if ohlcv_codes else 0, "%")

    db.close()


if __name__ == "__main__":
    main()
