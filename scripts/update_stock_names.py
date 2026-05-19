"""
scripts/update_stock_names.py

補齊 stock_meta 的股票中文名稱。

資料來源優先順序：
1. TWSE STOCK_DAY_ALL：用最近一個有行情資料的交易日抓 code/name。
2. MANUAL_STOCK_NAMES：少數常用 ETF / 權值股 / 使用者觀察清單當離線備援。

不會改 OHLCV / 分數，只會 upsert stock_meta。
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, StockMeta
from backend.utils.twse_client import twse_client

console = Console()

MANUAL_STOCK_NAMES = {
    "0050": "元大台灣50", "0051": "元大中型100", "0052": "富邦科技",
    "0053": "元大電子", "0054": "元大台商50", "0055": "元大MSCI金融",
    "0056": "元大高股息", "006208": "富邦台50", "00878": "國泰永續高股息",
    "00919": "群益台灣精選高息", "00981A": "主動統一台股增長",
    "1101": "台泥", "1216": "統一", "1301": "台塑", "1303": "南亞", "1326": "台化",
    "1402": "遠東新", "1802": "台玻", "2002": "中鋼", "2105": "正新", "2207": "和泰車",
    "2301": "光寶科", "2303": "聯電", "2308": "台達電", "2317": "鴻海", "2324": "仁寶",
    "2327": "國巨", "2330": "台積電", "2344": "華邦電", "2347": "聯強", "2352": "佳世達",
    "2354": "鴻準", "2356": "英業達", "2357": "華碩", "2371": "大同", "2377": "微星",
    "2379": "瑞昱", "2382": "廣達", "2383": "台光電", "2385": "群光", "2395": "研華",
    "2408": "南亞科", "2412": "中華電", "2448": "晶電", "2449": "京元電子", "2454": "聯發科",
    "2474": "可成", "2492": "華新科", "2498": "宏達電", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "2618": "長榮航", "2633": "台灣高鐵", "2801": "彰銀", "2881": "富邦金",
    "2882": "國泰金", "2886": "兆豐金", "2891": "中信金", "2912": "統一超", "3008": "大立光",
    "3034": "聯詠", "3037": "欣興", "3044": "健鼎", "3045": "台灣大", "3189": "景碩",
    "3481": "群創", "3711": "日月光投控", "4904": "遠傳", "4938": "和碩", "6205": "詮欣",
    "6446": "藥華藥", "6505": "台塑化", "6770": "力積電", "8046": "南電",
}


def _as_date(x):
    if isinstance(x, date):
        return x
    if x is None:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _latest_trade_date(db):
    row = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).fetchone()
    return _as_date(row[0] if row else None)


def _upsert_rows(db, rows):
    clean = []
    seen = set()
    for row in rows:
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        if not code or not name or code in seen:
            continue
        if name.lower() in ("nan", "none"):
            continue
        seen.add(code)
        clean.append({
            "code": code,
            "name": name,
            "market": row.get("market") or "TWSE",
            "is_active": True,
        })

    if not clean:
        return 0

    stmt = sqlite_insert(StockMeta).values(clean)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": stmt.excluded.name,
            "market": stmt.excluded.market,
            "is_active": True,
        },
    )
    db.execute(stmt)
    return len(clean)


def _fetch_twse_names(start_date: date, days_back: int = 14):
    for offset in range(days_back + 1):
        d = start_date - timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        console.print(f"[cyan]嘗試 TWSE STOCK_DAY_ALL:[/cyan] {d}")
        df = twse_client.fetch_daily_all(d)
        if df is None or df.empty or "name" not in df.columns:
            continue
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "code": str(r.get("code", "")).strip(),
                "name": str(r.get("name", "")).strip(),
                "market": "TWSE",
            })
        return d, rows
    return None, []


def main():
    db = SessionLocal()
    try:
        latest = _latest_trade_date(db) or date.today()
        fetched_date, rows = _fetch_twse_names(latest, days_back=21)
        added = _upsert_rows(db, rows)

        manual_rows = [
            {"code": code, "name": name, "market": "TWSE"}
            for code, name in MANUAL_STOCK_NAMES.items()
        ]
        manual_added = _upsert_rows(db, manual_rows)
        db.commit()

        total = db.execute(text("SELECT COUNT(*) FROM stock_meta")).scalar() or 0
        missing = db.execute(text("""
            SELECT COUNT(*)
            FROM (SELECT DISTINCT code FROM daily_scores) ds
            LEFT JOIN stock_meta sm ON sm.code = ds.code
            WHERE sm.name IS NULL OR TRIM(sm.name) = ''
        """)).scalar() or 0

        console.rule("[bold green]股票中文名稱更新完成")
        if fetched_date:
            console.print(f"TWSE 來源日期：{fetched_date}")
        else:
            console.print("[yellow]TWSE 抓取失敗，只使用手動備援名稱。[/yellow]")
        console.print(f"TWSE upsert：{added} 筆")
        console.print(f"手動備援 upsert：{manual_added} 筆")
        console.print(f"stock_meta 目前總筆數：{total}")
        console.print(f"daily_scores 仍缺名稱代號數：{missing}")
    finally:
        db.close()
        twse_client.close()


if __name__ == "__main__":
    main()
