"""scripts/v7_sector_rotation.py - 產業輪動分析"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from sqlalchemy import text
from backend.models.database import SessionLocal

THEMES = ["AI/伺服器", "半導體", "PCB/載板", "電源/散熱", "金融", "傳產", "航運", "記憶體"]

def update_sector_rotation(target_date: date = None):
    if not target_date:
        target_date = date.today()
    db = SessionLocal()
    try:
        inserted = 0
        rank = 1
        theme_scores = []

        for theme in THEMES:
            # 找主題成分股
            members = db.execute(text("""
                SELECT DISTINCT ttd.leader_codes FROM theme_trend_daily ttd
                WHERE ttd.theme LIKE :t AND ttd.context_date <= :d
                ORDER BY ttd.context_date DESC LIMIT 1
            """), {"t": f"%{theme.split('/')[0]}%", "d": str(target_date)}).fetchone()

            codes = []
            if members and members[0]:
                raw = str(members[0])
                import re
                codes = re.findall(r'\d{4,6}', raw)[:20]

            if not codes:
                continue

            # 計算主題績效
            perf = db.execute(text(f"""
                SELECT AVG(tdf.return_5d) as avg_5d,
                       AVG(tdf.return_1d) as avg_1d,
                       COUNT(*) as n
                FROM technical_daily_features tdf
                WHERE tdf.code IN ({','.join(f"'{c}'" for c in codes)})
                  AND tdf.trade_date = :d
            """), {"d": str(target_date)}).fetchone()

            if not perf or not perf[2]:
                continue

            avg_5d = float(perf[0] or 0)
            avg_1d = float(perf[1] or 0)

            # 籌碼強度
            chip = db.execute(text(f"""
                SELECT AVG(foreign_net + trust_net) FROM chip_daily
                WHERE code IN ({','.join(f"'{c}'" for c in codes)})
                  AND trade_date = :d
            """), {"d": str(target_date)}).scalar() or 0

            score = avg_5d * 0.5 + avg_1d * 0.3 + min(float(chip)/1000, 20) * 0.2

            theme_scores.append({
                "theme": theme,
                "avg_5d": avg_5d, "avg_1d": avg_1d,
                "chip": float(chip), "score": score,
                "n": perf[2]
            })

        # 排名
        theme_scores.sort(key=lambda x: x["score"], reverse=True)
        for i, ts in enumerate(theme_scores):
            db.execute(text("""
                INSERT INTO sector_theme_rotation
                    (trade_date, theme_name, stock_count, avg_return_1d, avg_return_5d,
                     chip_strength, theme_strength_score, rank)
                VALUES (:d,:t,:n,:r1,:r5,:cs,:ts,:rank)
                ON CONFLICT(trade_date, theme_name) DO UPDATE SET
                    avg_return_5d=excluded.avg_return_5d,
                    theme_strength_score=excluded.theme_strength_score,
                    rank=excluded.rank
            """), {"d": str(target_date), "t": ts["theme"], "n": ts["n"],
                   "r1": ts["avg_1d"], "r5": ts["avg_5d"],
                   "cs": ts["chip"], "ts": ts["score"], "rank": i+1})
            inserted += 1

        db.commit()
        print(f"[ROTATION] {target_date} 產業輪動更新：{inserted} 個主題")
        if theme_scores:
            print(f"  🏆 強勢：{theme_scores[0]['theme']}（{theme_scores[0]['avg_5d']:+.2f}%）")
        return inserted
    finally:
        db.close()


if __name__ == "__main__":
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    update_sector_rotation(d)
