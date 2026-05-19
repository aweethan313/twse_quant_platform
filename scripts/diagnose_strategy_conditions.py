"""
診斷為什麼某些策略不買。

用法：
    python -m scripts.diagnose_strategy_conditions
    python -m scripts.diagnose_strategy_conditions --date 2026-05-15
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, default=None)
    args = p.parse_args()

    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    score_date = args.date or cur.execute("SELECT MAX(score_date) FROM daily_scores").fetchone()[0]
    print(f"score_date = {score_date}")

    print("\n分數分布：")
    for col in [
        "fundamental_score", "valuation_score", "chip_score",
        "momentum_score", "macro_score", "news_score", "composite_score",
    ]:
        row = cur.execute(
            f"SELECT MIN({col}), AVG({col}), MAX({col}) FROM daily_scores WHERE score_date=?",
            (score_date,),
        ).fetchone()
        print(f"  {col:18s} min={row[0]:6.2f} avg={row[1]:6.2f} max={row[2]:6.2f}")

    print("\n原本策略條件命中數：")
    checks = [
        ("舊 Momentum：composite>=53 且 momentum>=68", "composite_score>=53 AND momentum_score>=68"),
        ("舊 Value：fundamental>=65 且 valuation>=60 且 chip>=55", "fundamental_score>=65 AND valuation_score>=60 AND chip_score>=55"),
        ("舊 Chip：chip>=70 且 news>=55 且 signal='BUY'", "chip_score>=70 AND news_score>=55 AND signal='BUY'"),
    ]
    for name, cond in checks:
        cnt = cur.execute(f"SELECT COUNT(*) FROM daily_scores WHERE score_date=? AND {cond}", (score_date,)).fetchone()[0]
        print(f"  {name}: {cnt}")

    print("\n新版策略條件初步命中數：")
    checks = [
        ("新 Momentum 初篩", "composite_score>=55 AND momentum_score>=62 AND chip_score>=42 AND macro_score>=42 AND news_score>=42"),
        ("新 Value 初篩", "fundamental_score>=52 AND valuation_score>=55 AND chip_score>=40 AND momentum_score>=28 AND macro_score>=40"),
        ("新 Chip 初篩", "chip_score>=56 AND news_score>=47 AND momentum_score>=45 AND composite_score>=50"),
        ("新 Balanced 初篩", "composite_score>=53 AND fundamental_score>=45 AND valuation_score>=45 AND chip_score>=42 AND momentum_score>=45 AND news_score>=45"),
    ]
    for name, cond in checks:
        cnt = cur.execute(f"SELECT COUNT(*) FROM daily_scores WHERE score_date=? AND {cond}", (score_date,)).fetchone()[0]
        print(f"  {name}: {cnt}")

    print("\n最新綜合分前 15 名：")
    for r in cur.execute(
        """
        SELECT code, composite_score, fundamental_score, valuation_score, chip_score, momentum_score, macro_score, news_score, signal
        FROM daily_scores
        WHERE score_date=?
        ORDER BY composite_score DESC
        LIMIT 15
        """,
        (score_date,),
    ):
        print("  ", r)
    conn.close()


if __name__ == "__main__":
    main()
