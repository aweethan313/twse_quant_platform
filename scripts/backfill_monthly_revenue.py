"""
scripts/backfill_monthly_revenue.py

補抓 monthly_revenue 月營收資料。

用法：
1. 補最近 18 個月：
   python -m scripts.backfill_monthly_revenue --months 18

2. 只補指定月份：
   python -m scripts.backfill_monthly_revenue --year 2026 --month 4
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.collectors.fundamental import run_monthly_revenue, backfill_monthly_revenue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=18)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--month", type=int, default=None)
    args = parser.parse_args()

    if args.year is not None or args.month is not None:
        if args.year is None or args.month is None:
            raise ValueError("--year 和 --month 要一起給")
        run_monthly_revenue(args.year, args.month)
    else:
        backfill_monthly_revenue(months=args.months)


if __name__ == "__main__":
    main()
