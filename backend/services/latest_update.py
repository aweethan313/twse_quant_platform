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
    """更新主線題材熱度 theme_trend_daily（用股票名稱關鍵字匹配）"""
    from loguru import logger
    from backend.models.database import SessionLocal
    from sqlalchemy import text
    import json

    THEME_KEYWORDS = {
        "AI/伺服器":   ["伺服器","散熱","液冷","機殼","緯穎","英業達","廣達","緯創","雙鴻","奇鋐","建準","超眾"],
        "半導體":      ["晶圓","封測","導體","矽力","積體","聯電","台積","日月光","矽格","華泰","南茂","菱生"],
        "PCB/載板":    ["PCB","電路板","載板","ABF","銅箔","基板","欣興","南電","台光","景碩","耀華"],
        "金融":        ["金","銀行","保險","壽險","票券","投信","富邦","國泰","中信","玉山","兆豐","合庫","第一","永豐","台新"],
        "航運":        ["航運","航空","貨櫃","散裝","長榮","陽明","萬海","裕民","慧洋"],
        "電動車":      ["電動","EV","充電","車用","電池","儲能","和泰","裕隆","正新","建大"],
        "生技醫療":    ["生技","醫療","製藥","疫苗","醫材","醫院","健康","藥","生醫","博晟","高端"],
        "網通/雲端":   ["網通","雲端","資安","軟體","系統","網路","數位","智慧","中磊","智邦","友訊"],
        "電子零組件":  ["被動元件","連接器","電源","鏡頭","感測","國巨","華新科","禾伸堂","信昌電"],
        "傳產":        ["鋼鐵","塑化","紡織","水泥","橡膠","石化","中鋼","台塑","南亞","亞泥","台泥"],
    }

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT o.code, o.change_pct, o.volume,
                   (SELECT AVG(v.volume) FROM ohlcv_daily v
                    WHERE v.code=o.code AND v.trade_date<:d
                    AND v.trade_date>date(:d,'-20 days')) as avg_vol,
                   COALESCE(sm.name, o.code) as name
            FROM ohlcv_daily o
            LEFT JOIN stock_meta sm ON sm.code=o.code
            WHERE o.trade_date=:d AND o.change_pct IS NOT NULL
        """), {"d": str(target_date)}).fetchall()

        if not rows:
            return {"ok": False, "message": "無今日行情資料"}

        updated = 0
        for theme_name, keywords in THEME_KEYWORDS.items():
            stocks = []
            for code, chg, vol, avg_vol, name in rows:
                if any(k in (name or "") for k in keywords):
                    stocks.append({
                        "code": code,
                        "change_pct": float(chg or 0),
                        "vol_ratio": float(vol or 1) / float(avg_vol or 1) if avg_vol else 1.0,
                    })

            if not stocks:
                continue

            avg_chg  = sum(s["change_pct"] for s in stocks) / len(stocks)
            avg_vr   = sum(s["vol_ratio"]  for s in stocks) / len(stocks)
            up_count = sum(1 for s in stocks if s["change_pct"] > 0)
            breadth  = up_count / len(stocks) * 100
            score    = min(100, max(0, 50 + avg_chg * 3 + (avg_vr-1)*10 + (breadth-50)*0.4))
            momentum = min(100, max(0, 50 + avg_chg * 5))
            leaders  = sorted(stocks, key=lambda x: x["change_pct"], reverse=True)[:3]
            leader_codes = json.dumps([s["code"] for s in leaders], ensure_ascii=False)

            db.execute(text("""
                INSERT INTO theme_trend_daily
                    (context_date, theme, score, momentum_score, breadth_score,
                     volume_ratio, code_count, leader_codes, summary)
                VALUES (:d,:theme,:score,:ms,:bs,:vr,:cc,:lc,:summary)
                ON CONFLICT(context_date, theme) DO UPDATE SET
                    score=excluded.score, momentum_score=excluded.momentum_score,
                    breadth_score=excluded.breadth_score, volume_ratio=excluded.volume_ratio,
                    code_count=excluded.code_count, leader_codes=excluded.leader_codes,
                    summary=excluded.summary
            """), {
                "d": str(target_date), "theme": theme_name,
                "score": round(score,1), "ms": round(momentum,1),
                "bs": round(breadth,1), "vr": round(avg_vr,2),
                "cc": len(stocks), "lc": leader_codes,
                "summary": f"{theme_name} {len(stocks)}檔 均{avg_chg:+.2f}% 廣度{breadth:.0f}%",
            })
            updated += 1

        db.commit()
        logger.info(f"[THEME] {target_date} 更新 {updated} 個主題")
        return {"ok": True, "themes_updated": updated}
    except Exception as e:
        logger.error(f"[THEME] 失敗: {e}")
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()



def _snapshot_equity(target_date) -> dict:
    """每日收盤後快照所有策略帳戶的估值"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text
    from loguru import logger
    db = SessionLocal()
    try:
        accounts = db.execute(text("SELECT id FROM strategy_accounts")).fetchall()
        updated = 0
        for (aid,) in accounts:
            # 取最新持倉市值
            mkt = db.execute(text("""
                SELECT SUM(p.shares * o.close)
                FROM positions p
                LEFT JOIN ohlcv_daily o ON o.code=p.code
                  AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
                WHERE p.account_id=:aid
            """), {"aid": aid}).scalar() or 0

            # 取現金（最新 equity_curve 的 cash，或估算）
            last_eq = db.execute(text("""
                SELECT cash, total_equity FROM equity_curve
                WHERE account_id=:aid ORDER BY snap_date DESC LIMIT 1
            """), {"aid": aid}).fetchone()

            cash = float(last_eq[0] or 0) if last_eq else 200000
            total = cash + float(mkt or 0)
            if total <= 0: total = float(last_eq[1] or 200000) if last_eq else 200000

            # 計算日報酬
            prev = db.execute(text("""
                SELECT total_equity FROM equity_curve
                WHERE account_id=:aid AND snap_date < :d
                ORDER BY snap_date DESC LIMIT 1
            """), {"aid": aid, "d": str(target_date)}).scalar()
            daily_ret = (total / float(prev) - 1) * 100 if prev and float(prev) > 0 else 0

            db.execute(text("""
                INSERT INTO equity_curve (account_id, snap_date, cash, market_value, total_equity, daily_return)
                VALUES (:aid, :d, :cash, :mkt, :total, :ret)
                ON CONFLICT(account_id, snap_date) DO UPDATE SET
                    cash=excluded.cash, market_value=excluded.market_value,
                    total_equity=excluded.total_equity, daily_return=excluded.daily_return
            """), {"aid": aid, "d": str(target_date), "cash": cash,
                   "mkt": float(mkt or 0), "total": total, "ret": daily_ret})
            updated += 1

        db.commit()
        logger.info(f"[EQUITY] {target_date} 快照 {updated} 個帳戶")
        return {"ok": True, "updated": updated}
    except Exception as e:
        db.rollback()
        logger.error(f"[EQUITY] 快照失敗: {e}")
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
        steps.append(_step("equity_snapshot", lambda: _snapshot_equity(target_date)))

        ok = all(s["ok"] for s in steps)

        return {
            "ok": ok,
            "trade_date": str(target_date),
            "steps": steps,
        }

    finally:
        _UPDATE_LOCK.release()
