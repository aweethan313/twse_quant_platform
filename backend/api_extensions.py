"""backend/api_extensions.py

把幾個「加值」端點集中在這裡，用一行 register_extensions(app, templates) 掛進 main.py，
避免再往 3000+ 行的 main.py 裡塞東西。

提供：
  GET /api/data-health/staleness  → 真實僵屍列偵測（close 與 value 都和前一日相同 = 帶上來的死資料）
  GET /api/ml-picks               → 當日 ML 選股 Top N，並標出規則引擎是否也看好（ML ∩ 規則）
  GET /ml-picks                   → ML 選股頁（ml_picks.html）
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse
from backend.models.database import SessionLocal
from sqlalchemy import text as _t


def _staleness(days: int = 60) -> dict:
    """最近 days 天，四位數代號普通股裡，close 與 value 都和前一交易日完全相同的比例。"""
    db = SessionLocal()
    try:
        latest = db.execute(_t("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()
        sql = _t("""
            WITH base AS (
                SELECT code, trade_date, close, value,
                    LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS pc,
                    LAG(value) OVER (PARTITION BY code ORDER BY trade_date) AS pv
                FROM ohlcv_daily
                WHERE code GLOB '[0-9][0-9][0-9][0-9]'
                  AND trade_date >= date(:latest, '-' || :days || ' days')
            )
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN close = pc AND value = pv THEN 1 ELSE 0 END) AS stale
            FROM base WHERE pc IS NOT NULL
        """)
        row = db.execute(sql, {"latest": latest, "days": days}).fetchone()
        total = row[0] or 0
        stale = row[1] or 0
        pct = round(100 * stale / total, 1) if total else 0.0
        # 健康度判定：< 15% 視為健康（停牌等正常情況），>= 30% 視為有資料問題
        status = "healthy" if pct < 15 else ("warning" if pct < 30 else "bad")
        return {"window_days": days, "as_of": latest, "total_rows": total,
                "stale_rows": stale, "stale_pct": pct, "status": status}
    finally:
        db.close()


def _ml_picks(limit: int = 20) -> dict:
    """當日 ML 選股 Top N，左 join 規則分數，標出規則是否也看好（overlap）。"""
    db = SessionLocal()
    try:
        sd = db.execute(_t("SELECT MAX(score_date) FROM ml_score_results")).scalar()
        if not sd:
            return {"score_date": None, "picks": [],
                    "note": "ml_score_results 是空的，請先跑 ml_scorer.py"}
        sql = _t("""
            SELECT m.code, m.stock_name, m.ml_score, m.ml_rank, m.predicted_return_5d,
                   d.final_score, d.final_action
            FROM ml_score_results m
            LEFT JOIN daily_scores d
                   ON d.code = m.code AND d.score_date = m.score_date
            WHERE m.score_date = :sd
            ORDER BY m.ml_rank
            LIMIT :n
        """)
        rows = db.execute(sql, {"sd": sd, "n": limit}).fetchall()
        picks = []
        for r in rows:
            action = r[6]
            picks.append({
                "code": r[0], "name": r[1], "ml_score": r[2], "ml_rank": r[3],
                "pred_5d": r[4], "final_score": r[5], "final_action": action,
                # 規則引擎也看好（BUY/WATCH）= 兩套訊號交集，信心更高
                "rule_agrees": action in ("BUY", "WATCH") if action else False,
            })
        n_overlap = sum(1 for p in picks if p["rule_agrees"])
        return {"score_date": sd, "count": len(picks),
                "overlap_with_rules": n_overlap, "picks": picks}
    finally:
        db.close()


def register_extensions(app, templates):
    """在 main.py 建立 app 與 templates 之後呼叫一次即可。"""
    @app.get("/api/data-health/staleness")
    def api_data_health_staleness(days: int = 60):
        return _staleness(days)

    @app.get("/api/ml-picks")
    def api_ml_picks(limit: int = 20):
        return _ml_picks(limit)

    @app.get("/ml-picks", response_class=HTMLResponse)
    def page_ml_picks(request: Request):
        return templates.TemplateResponse("ml_picks.html", {"request": request})
