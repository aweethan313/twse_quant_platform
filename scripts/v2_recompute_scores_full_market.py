from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

from backend.models.database import SessionLocal
from backend.signals.scorer import compute_scores


START_DATE = date(2025, 1, 1)
END_DATE = date(2026, 5, 21)

REPORT_DIR = Path("data/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_codes_for_date(db, d: date) -> list[str]:
    rows = db.execute(
        text("""
            SELECT DISTINCT code
            FROM ohlcv_daily
            WHERE trade_date = :d
              AND close IS NOT NULL
              AND close > 0
            ORDER BY code
        """),
        {"d": str(d)},
    ).fetchall()
    return [r[0] for r in rows]


def count_scores_for_date(db, d: date) -> int:
    return int(db.execute(
        text("""
            SELECT COUNT(*)
            FROM daily_scores
            WHERE score_date = :d
        """),
        {"d": str(d)},
    ).scalar() or 0)


def main():
    db = SessionLocal()

    d = START_DATE
    total_days = 0
    ok_days = 0
    skipped_days = 0
    failed_days = 0

    report_rows = []

    while d <= END_DATE:
        # 週末先跳過；國定假日若 DB 沒資料，也會自動跳過
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue

        codes = get_codes_for_date(db, d)

        if not codes:
            print(f"{d} SKIP no ohlcv")
            skipped_days += 1
            d += timedelta(days=1)
            continue

        total_days += 1

        try:
            compute_scores(codes, d)
            score_count = count_scores_for_date(db, d)
            coverage = score_count / len(codes) if codes else 0

            print(
                f"{d} OK codes={len(codes)} scores={score_count} "
                f"coverage={coverage:.2%}",
                flush=True,
            )

            report_rows.append(
                f"{d},{len(codes)},{score_count},{coverage:.4f},OK\n"
            )
            ok_days += 1

        except Exception as e:
            print(f"{d} ERR {e}", flush=True)
            report_rows.append(
                f"{d},{len(codes)},0,0.0000,ERR:{str(e).replace(',', ' ')}\n"
            )
            failed_days += 1

        d += timedelta(days=1)

    db.close()

    report_path = REPORT_DIR / "v2_full_market_score_rebuild_report.csv"
    report_path.write_text(
        "date,ohlcv_codes,score_count,coverage,status\n" + "".join(report_rows),
        encoding="utf-8",
    )

    print("=" * 80)
    print("V2 full-market score rebuild finished")
    print(f"ok_days={ok_days}, skipped_days={skipped_days}, failed_days={failed_days}")
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
