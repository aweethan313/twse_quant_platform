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

        ok = all(s["ok"] for s in steps)

        return {
            "ok": ok,
            "trade_date": str(target_date),
            "steps": steps,
        }

    finally:
        _UPDATE_LOCK.release()
