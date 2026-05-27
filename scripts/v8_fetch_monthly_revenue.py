"""scripts/v8_fetch_monthly_revenue.py - 月營收自動收集（MOPS）"""
import sys, os, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal
from loguru import logger


def fetch_mops_revenue(year: int, month: int) -> list:
    """從 MOPS 抓月營收資料"""
    # MOPS 月營收 API
    tw_year = year - 1911
    url = "https://mops.twse.com.tw/nas/t21/sii/t21sc03_{year}_{month}_0.html".format(
        year=tw_year, month=month
    )
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    results = []
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.warning(f"[REVENUE] {year}/{month} HTTP {r.status_code}")
            return []

        from html.parser import HTMLParser
        import re

        # 簡單解析 HTML 表格
        text_content = r.text
        # 找所有 <tr> 行
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text_content, re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) < 6:
                continue
            clean = lambda s: re.sub(r'<[^>]+>', '', s).strip().replace(',', '')
            code = clean(cells[0])
            name = clean(cells[1])
            if not re.match(r'^\d{4,6}$', code):
                continue
            try:
                revenue = float(clean(cells[2])) if clean(cells[2]) else None
                revenue_yoy = float(clean(cells[5]).replace('%','')) if len(cells) > 5 and clean(cells[5]) else None
                revenue_mom = float(clean(cells[4]).replace('%','')) if len(cells) > 4 and clean(cells[4]) else None
                if revenue:
                    results.append({"code": code, "name": name,
                                    "revenue": revenue, "yoy": revenue_yoy, "mom": revenue_mom})
            except (ValueError, IndexError):
                continue

        logger.info(f"[REVENUE] {year}/{month} 抓到 {len(results)} 筆")
        return results

    except Exception as e:
        logger.warning(f"[REVENUE] {year}/{month} 失敗: {e}")
        return []


def save_revenue(db, year: int, month: int, data: list, announce_date: str):
    saved = 0
    for r in data:
        try:
            db.execute(text("""
                INSERT INTO monthly_revenue
                    (code, stock_name, year, month, revenue,
                     revenue_yoy, revenue_mom, announce_date)
                VALUES (:c,:n,:y,:m,:rev,:yoy,:mom,:ad)
                ON CONFLICT(code, year, month) DO UPDATE SET
                    revenue=excluded.revenue,
                    revenue_yoy=excluded.revenue_yoy,
                    revenue_mom=excluded.revenue_mom
            """), {"c": r["code"], "n": r["name"], "y": year, "m": month,
                   "rev": r["revenue"], "yoy": r["yoy"], "mom": r["mom"],
                   "ad": announce_date})
            saved += 1
        except Exception:
            pass
    db.commit()
    return saved


def run(months_back: int = 3):
    db = SessionLocal()
    today = date.today()
    total = 0

    for i in range(months_back):
        # 計算目標月份
        m = today.month - i - 1
        y = today.year
        while m <= 0:
            m += 12
            y -= 1

        # 公告日（通常每月 10 日）
        announce_date = f"{y}-{m+1:02d}-10" if m < 12 else f"{y+1}-01-10"

        print(f"抓取 {y}年{m}月 營收...")
        data = fetch_mops_revenue(y, m)
        if data:
            n = save_revenue(db, y, m, data, announce_date)
            total += n
            print(f"  ✓ 儲存 {n} 筆")

            # 印出 YoY 最強前5
            top = sorted([d for d in data if d["yoy"]], key=lambda x: -(x["yoy"] or 0))[:5]
            for t in top:
                print(f"  🔥 {t['code']} {t['name']} YoY={t['yoy']:+.1f}%")
        else:
            print(f"  ⚠️ 無資料（可能尚未公布）")
        time.sleep(1)

    db.close()
    print(f"\n✓ 月營收共儲存 {total} 筆")

if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    run(months)
