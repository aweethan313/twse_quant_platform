from __future__ import annotations

import importlib
import threading
import traceback
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from backend.collectors.daily_eod import run_eod
from backend.models.database import SessionLocal
from backend.signals.scorer import compute_scores


_UPDATE_LOCK = threading.Lock()


def _parse_trade_date(trade_date: str | None) -> date:
    if trade_date:
        return datetime.strptime(trade_date, "%Y-%m-%d").date()
    return date.today()


def _step(name: str, fn):
    try:
        result = fn()
        return {
            "name": name,
            "ok": True,
            "result": result,
            "error": None,
        }
    except Exception as e:
        return {
            "name": name,
            "ok": False,
            "result": None,
            "error": str(e),
            "traceback": traceback.format_exc(limit=6),
        }


def update_overnight() -> dict[str, Any]:
    mod = importlib.import_module("backend.collectors.overnight_market")

    if hasattr(mod, "get_overnight_summary"):
        summary = mod.get_overnight_summary(force_refresh=True)
        bias = summary.get("bias", {}) if isinstance(summary, dict) else {}
        symbols = summary.get("symbols", {}) if isinstance(summary, dict) else {}

        return {
            "status": "done",
            "overnight_score": bias.get("score"),
            "overall": bias.get("overall"),
            "symbols": {
                k: {
                    "name": v.get("name"),
                    "ret": v.get("ret"),
                    "close": v.get("close"),
                }
                for k, v in symbols.items()
            },
        }

    data = mod.fetch_overnight()
    bias = mod.compute_bias(data)

    if hasattr(mod, "save_to_db"):
        mod.save_to_db(data, bias)

    return {
        "status": "done",
        "overnight_score": bias.get("score"),
        "overall": bias.get("overall"),
    }

def update_daily_eod(target_date: date) -> dict[str, Any]:
    run_eod(target_date)

    db = SessionLocal()
    try:
        daily_count = db.execute(
            text("""
                SELECT COUNT(*)
                FROM ohlcv_daily
                WHERE trade_date = :d
            """),
            {"d": target_date},
        ).scalar()

        chip_count = db.execute(
            text("""
                SELECT COUNT(*)
                FROM chip_daily
                WHERE trade_date = :d
            """),
            {"d": target_date},
        ).scalar()

        return {
            "status": "done",
            "trade_date": str(target_date),
            "ohlcv_daily_rows": int(daily_count or 0),
            "chip_daily_rows": int(chip_count or 0),
        }
    finally:
        db.close()


def get_codes_for_date(target_date: date) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT DISTINCT code
                FROM ohlcv_daily
                WHERE trade_date = :d
                ORDER BY code
            """),
            {"d": target_date},
        ).fetchall()
        return [str(r[0]) for r in rows if r[0]]
    finally:
        db.close()


def recompute_scores_for_date(target_date: date) -> dict[str, Any]:
    codes = get_codes_for_date(target_date)

    if not codes:
        return {
            "status": "skipped",
            "reason": f"{target_date} 沒有 ohlcv_daily 資料，所以不重算分數",
            "codes": 0,
        }

    compute_scores(codes, target_date)

    db = SessionLocal()
    try:
        score_count = db.execute(
            text("""
                SELECT COUNT(*)
                FROM daily_scores
                WHERE score_date = :d
            """),
            {"d": target_date},
        ).scalar()

        return {
            "status": "done",
            "trade_date": str(target_date),
            "codes": len(codes),
            "daily_scores_rows": int(score_count or 0),
        }
    finally:
        db.close()




def update_theme_trends(target_date) -> dict:
    """更新主線題材熱度 theme_trend_daily"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text
    import json
    db = SessionLocal()
    try:
        # 預定義主題清單 + 對應關鍵字/產業
        THEMES = {
            "AI":       ["AI", "人工智", "伺服器", "散熱", "CoWoS", "HBM", "GB200"],
            "半導體":   ["半導體", "晶圓", "IC設計", "封測", "積體電路"],
            "PCB":      ["PCB", "電路板", "載板", "ABF"],
            "電動車":   ["電動車", "EV", "充電", "車用"],
            "金融":     ["銀行", "金控", "保險", "壽險"],
            "航運":     ["航運", "貨櫃", "散裝", "航空"],
            "生技":     ["生技", "醫療", "製藥", "疫苗"],
            "傳產":     ["鋼鐵", "塑化", "紡織", "水泥"],
            "電子零組件": ["被動元件", "連接器", "電源", "鏡頭"],
            "雲端":     ["雲端", "資安", "SaaS", "軟體"],
        }

        # 取今日漲跌幅資料
        price_rows = db.execute(text("""
            SELECT o.code, o.change_pct, o.volume,
                   (SELECT AVG(v.volume) FROM ohlcv_daily v
                    WHERE v.code=o.code AND v.trade_date<:d
                    AND v.trade_date>date(:d,'-20 days')) as avg_vol,
                   sm.industry
            FROM ohlcv_daily o
            LEFT JOIN stock_meta sm ON sm.code=o.code
            WHERE o.trade_date=:d AND o.change_pct IS NOT NULL
        """), {"d": str(target_date)}).fetchall()

        if not price_rows:
            return {"ok": False, "message": "無今日行情資料"}

        # 也取 theme_score 欄位（若有）
        news_themes = {}
        try:
            ns = db.execute(text("""
                SELECT related_themes FROM candidate_news
                WHERE news_time >= date(:d, '-3 days')
            """), {"d": str(target_date)}).fetchall()
            for row in ns:
                if row[0]:
                    for t in str(row[0]).split(","):
                        t = t.strip()
                        if t: news_themes[t] = news_themes.get(t, 0) + 1
        except Exception:
            pass

        updated = 0
        now = str(target_date)

        for theme_name, keywords in THEMES.items():
            # 找屬於這個主題的股票
            theme_stocks = []
            for row in price_rows:
                code, chg, vol, avg_vol, industry = row
                industry = industry or ""
                match = any(k in industry for k in keywords)
                if match:
                    theme_stocks.append({
                        "code": code,
                        "change_pct": float(chg or 0),
                        "vol_ratio": float(vol or 0) / float(avg_vol or 1) if avg_vol else 1.0,
                    })

            if not theme_stocks:
                continue

            avg_chg    = sum(s["change_pct"] for s in theme_stocks) / len(theme_stocks)
            avg_vr     = sum(s["vol_ratio"]  for s in theme_stocks) / len(theme_stocks)
            up_count   = sum(1 for s in theme_stocks if s["change_pct"] > 0)
            breadth    = up_count / len(theme_stocks) * 100
            code_count = len(theme_stocks)

            # 主題分數：漲幅 + 量能 + 廣度
            score = min(100, max(0,
                50 +
                avg_chg * 3 +
                (avg_vr - 1) * 10 +
                (breadth - 50) * 0.4
            ))

            # 領漲股（漲幅前3）
            leaders = sorted(theme_stocks, key=lambda x: x["change_pct"], reverse=True)[:3]
            leader_codes = json.dumps([s["code"] for s in leaders], ensure_ascii=False)

            # 新聞熱度加分
            news_boost = sum(v for k, v in news_themes.items()
                             if any(kw in k for kw in keywords)) * 2

            score = min(100, score + news_boost)
            momentum = min(100, max(0, 50 + avg_chg * 5))

            # 寫入（upsert）
            db.execute(text("""
                INSERT INTO theme_trend_daily
                    (context_date, theme, score, momentum_score, breadth_score,
                     volume_ratio, code_count, leader_codes, summary)
                VALUES (:d, :theme, :score, :ms, :bs, :vr, :cc, :lc, :summary)
                ON CONFLICT(context_date, theme) DO UPDATE SET
                    score=excluded.score,
                    momentum_score=excluded.momentum_score,
                    breadth_score=excluded.breadth_score,
                    volume_ratio=excluded.volume_ratio,
                    code_count=excluded.code_count,
                    leader_codes=excluded.leader_codes,
                    summary=excluded.summary
            """), {
                "d": str(target_date), "theme": theme_name,
                "score": round(score, 1), "ms": round(momentum, 1),
                "bs": round(breadth, 1), "vr": round(avg_vr, 2),
                "cc": code_count, "lc": leader_codes,
                "summary": f"{theme_name} {len(theme_stocks)}檔 均漲{avg_chg:+.2f}% 廣度{breadth:.0f}%",
            })
            updated += 1

        db.commit()
        logger.info(f"[THEME] {target_date} 更新 {updated} 個主題")
        return {"ok": True, "themes_updated": updated}
    except Exception as e:
        logger.error(f"[THEME] 更新失敗: {e}")
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()

def run_latest_update(trade_date: str | None = None) -> dict[str, Any]:
    if not _UPDATE_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "status": "running",
            "message": "已經有一個更新任務正在執行，請稍後再試。",
        }

    try:
        target_date = _parse_trade_date(trade_date)

        steps = []
        steps.append(_step("overnight", update_overnight))
        steps.append(_step("daily_eod", lambda: update_daily_eod(target_date)))
        steps.append(_step("scores", lambda: recompute_scores_for_date(target_date)))
        steps.append(_step("theme_trends", lambda: update_theme_trends(target_date)))

        ok = all(s["ok"] for s in steps)

        return {
            "ok": ok,
            "trade_date": str(target_date),
            "steps": steps,
        }

    finally:
        _UPDATE_LOCK.release()
