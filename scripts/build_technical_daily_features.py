"""scripts/build_technical_daily_features.py
計算今日（或指定日期）所有股票的技術指標
用法：
  python3 -m scripts.build_technical_daily_features          # 今日
  python3 -m scripts.build_technical_daily_features 2026-05-24  # 指定日期
  python3 -m scripts.build_technical_daily_features --all    # 補算所有日期
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from backend.services.technical_features import build_technical_features, get_coverage_stats
from backend.models.database import SessionLocal
from sqlalchemy import text

def main():
    args = sys.argv[1:]

    if "--all" in args:
        # 補算所有日期
        db = SessionLocal()
        dates = db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date >= '2025-01-01'
            ORDER BY trade_date
        """)).fetchall()
        db.close()
        print(f"補算 {len(dates)} 個交易日...")
        for i, (td,) in enumerate(dates):
            n = build_technical_features(date.fromisoformat(td))
            if (i+1) % 10 == 0:
                print(f"  {i+1}/{len(dates)} {td} {n}檔")
        print("✓ 補算完成")
    elif args and args[0] != "--all":
        td = date.fromisoformat(args[0])
        print(f"計算 {td}...")
        n = build_technical_features(td)
        print(f"✓ {td} {n} 檔完成")
    else:
        td = date.today()
        print(f"=== 技術指標計算 {td} ===")
        n = build_technical_features(td)
        print(f"✓ {n} 檔完成")

    # 覆蓋率統計
    stats = get_coverage_stats()
    print(f"\n覆蓋率: {stats['coverage_pct']}% ({stats['total_tech']}/{stats['total_ohlcv']})")
    print(f"缺 MA20: {stats['missing_ma20']} 檔（資料不足20天）")
    print(f"缺 RSI:  {stats['missing_rsi']} 檔")

if __name__ == "__main__":
    main()
