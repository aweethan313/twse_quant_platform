import math
"""
main.py  –  FastAPI 入口
"""
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, timedelta
from typing import Optional
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from config.settings import settings
from backend.models.database import (
    get_db, init_db, StrategyAccount, Position, TradeLog, OHLCVDaily, DailyScore, StockMeta
)
from backend.engine.paper_account import PaperAccount
from backend.engine.strategy_runner import get_competition_ranking
from backend.strategies.base_strategy import STRATEGY_REGISTRY

import os

app = FastAPI(title=settings.APP_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Static files + templates
os.makedirs("frontend/static", exist_ok=True)
os.makedirs("frontend/templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
templates = Jinja2Templates(directory="frontend/templates")


@app.on_event("startup")
def startup():
    init_db()


# ══════════════════════════════════════════════
# HTML Pages
# ══════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def page_market(request: Request):
    return templates.TemplateResponse(request=request, name="market.html", context={})

@app.get("/strategies", response_class=HTMLResponse)
async def page_strategies(request: Request):
    return templates.TemplateResponse(request=request, name="strategies.html", context={})


@app.get("/candidates", response_class=HTMLResponse)
def page_candidates(request: Request):
    return templates.TemplateResponse("candidates.html", {"request": request})
@app.get("/competition", response_class=HTMLResponse)
async def page_competition(request: Request):
    return templates.TemplateResponse(request=request, name="competition.html", context={})

@app.get("/stock/{code}", response_class=HTMLResponse)
async def page_stock(request: Request, code: str):
    return templates.TemplateResponse(request=request, name="stock_detail.html", context={"code": code})


# ══════════════════════════════════════════════
# API: 行情
# ══════════════════════════════════════════════

@app.get("/api/stock_names")
def api_stock_names(db: Session = Depends(get_db)):
    """股票中文名稱對照表，給前端動態補齊名稱。"""
    rows = db.execute(
        text("""
            SELECT code, name
            FROM stock_meta
            WHERE name IS NOT NULL
              AND TRIM(name) != ''
            ORDER BY code
        """)
    ).fetchall()
    return {"names": {str(r[0]): str(r[1]) for r in rows if r[0] and r[1]}}


@app.get("/api/stock/{code}/meta")
def api_stock_meta(code: str, db: Session = Depends(get_db)):
    """單一股票基本資料。"""
    row = db.execute(
        text("""
            SELECT code, name, market, industry
            FROM stock_meta
            WHERE code = :code
            LIMIT 1
        """),
        {"code": code},
    ).fetchone()
    if not row:
        return {"code": code, "name": code, "market": None, "industry": None}
    return {"code": row[0], "name": row[1], "market": row[2], "industry": row[3]}



def get_latest_trade_date(db) -> str:
    """從 ohlcv_daily 找最新有效交易日，避免假日/盤後回傳空資料"""
    from sqlalchemy import text as _text
    row = db.execute(_text(
        "SELECT MAX(trade_date) FROM ohlcv_daily WHERE trade_date <= date('now')"
    )).fetchone()
    return str(row[0]) if row and row[0] else str(date.today())



@app.get("/api/stocks/rankings")
def api_stocks_rankings(
    rank_mode: str = "final",
    max_risk_score: float = 100,
    min_risk_score: float = 0,
    stock_class: str = None,
    core_only: bool = False,
    final_action: str = None,
    limit: int = 30,
    db: Session = Depends(get_db)
):
    import json as _json
    FLAG_ZH = {
        "short_term_overheat":"短線過熱","too_far_from_ma":"離均線過遠",
        "rsi_overheated":"RSI過熱","consecutive_limit_up":"連續漲停",
        "high_volume_upper_shadow":"爆量長上影","gap_up_fade":"開高走低",
        "high_volume_black_candle":"爆量黑K","limit_up_opened":"漲停打開",
        "hot_money_day_trade_risk":"隔日沖風險","price_volume_divergence":"量價背離",
        "institutions_sell_retail_buy":"法人賣散戶接",
    }
    CLASS_ZH = {
        "ETF_CORE":"核心ETF","ETF_INCOME":"收益ETF",
        "CORE_LARGE_CAP":"核心大型股","LARGE_LIQUID":"中大型流動",
        "LIQUID_MOMENTUM":"流動強勢","SPECULATIVE_HOT":"短線題材",
        "ILLIQUID_RISK":"低流動性","NORMAL":"一般股票",
    }
    base_q = """
        SELECT ds.code, sm.name, ds.composite_score, ds.signal,
               ds.candidate_score, ds.entry_score, ds.risk_score,
               ds.risk_flags, ds.final_score, ds.final_action,
               ds.core_score, ds.stock_class, ds.volume_score,
               o.close, o.change_pct
        FROM daily_scores ds
        LEFT JOIN stock_meta sm ON sm.code = ds.code
        LEFT JOIN ohlcv_daily o ON o.code = ds.code
            AND o.trade_date = (SELECT MAX(trade_date) FROM ohlcv_daily)
        WHERE ds.score_date = (SELECT MAX(score_date) FROM daily_scores)
          AND (ds.risk_score IS NULL OR ds.risk_score <= :max_risk)
          AND (ds.risk_score IS NULL OR ds.risk_score >= :min_risk)
    """
    params = {"max_risk": max_risk_score, "min_risk": min_risk_score, "limit": limit}
    if stock_class:
        base_q += " AND ds.stock_class = :sc"; params["sc"] = stock_class
    if core_only:
        base_q += " AND ds.stock_class = 'CORE_LARGE_CAP'"
    if final_action:
        base_q += " AND ds.final_action = :fa"; params["fa"] = final_action
    if rank_mode == "avoid_chase":
        base_q += " AND (ds.final_action = 'AVOID_CHASE' OR ds.risk_score >= 60)"
    if rank_mode == "core":
        base_q += " AND ds.stock_class = 'CORE_LARGE_CAP'"
    ORDER = {"final":"ds.final_score DESC, ds.risk_score ASC",
             "candidate":"ds.candidate_score DESC",
             "core":"ds.core_score DESC, ds.risk_score ASC",
             "avoid_chase":"ds.candidate_score DESC, ds.risk_score DESC",
             "risk":"ds.risk_score DESC"}
    rows = db.execute(text(base_q + f" ORDER BY {ORDER.get(rank_mode,'ds.final_score DESC')} LIMIT :limit"), params).fetchall()
    result = []
    for r in rows:
        flags = []
        try:
            if r[7]: flags = _json.loads(r[7])
        except Exception: pass
        result.append({
            "code":r[0],"name":r[1] or r[0],
            "composite":round(float(r[2] or 0),2),"signal":r[3],
            "candidate_score":round(float(r[4] or r[2] or 0),2),
            "entry_score":round(float(r[5] or 50),2),
            "risk_score":round(float(r[6] or 30),2),
            "risk_flags":flags,
            "risk_flags_zh":[FLAG_ZH.get(f,f) for f in flags],
            "final_score":round(float(r[8] or r[2] or 0),2),
            "final_action":r[9] or r[3],
            "core_score":round(float(r[10] or r[2] or 0),2),
            "stock_class":r[11] or "NORMAL","stock_class_zh":CLASS_ZH.get(r[11] or "NORMAL","一般股票"),
            "volume_score":round(float(r[12] or 50),2),
            "close":r[13],"change_pct":r[14],
        })
    return result

@app.get("/api/stocks/names")
def api_stocks_names(db: Session = Depends(get_db)):
    """所有股票代號 → 中文名稱對照表"""
    rows = db.execute(text("SELECT code, name FROM stock_meta WHERE name IS NOT NULL AND name != '' ORDER BY code")).fetchall()
    return {r[0]: r[1] for r in rows}

@app.get("/api/market/overview")
def api_market_overview(db: Session = Depends(get_db)):
    """大盤概覽：今日漲跌家數、成交值"""
    today = get_latest_trade_date(db)
    rows = db.execute(
        text("""
            SELECT
              SUM(CASE WHEN change>0 THEN 1 ELSE 0 END) as up_count,
              SUM(CASE WHEN change<0 THEN 1 ELSE 0 END) as dn_count,
              SUM(CASE WHEN change=0 THEN 1 ELSE 0 END) as flat_count,
              SUM(value) as total_value
            FROM ohlcv_daily WHERE trade_date=:d
        """), {"d": today}
    ).fetchone()
    return {
        "trade_date": str(today), "latest_trade_date": str(today),
        "up": int(rows[0] or 0),
        "down": int(rows[1] or 0),
        "flat": int(rows[2] or 0),
        "total_value_bn": round((rows[3] or 0) / 1e5, 2),  # 億
    }


@app.get("/api/market/top_movers")
def api_top_movers(limit: int = 20, db: Session = Depends(get_db)):
    """今日漲跌幅排行"""
    today = get_latest_trade_date(db)
    rows = db.execute(
        text("""
            SELECT o.code, sm.name, o.close, o.change, o.change_pct, o.volume
            FROM ohlcv_daily o
            LEFT JOIN stock_meta sm ON sm.code = o.code
            WHERE o.trade_date=:d
            ORDER BY o.change_pct DESC LIMIT :n
        """), {"d": today, "n": limit}
    ).fetchall()
    return [{"code":r[0],"name":r[1] or r[0],"close":r[2],"change":r[3],"change_pct":r[4],"volume":r[5]}
            for r in rows]


@app.get("/api/stock/{code}/kline")
def api_kline(code: str, days: int = 60, db: Session = Depends(get_db)):
    """個股日K"""
    rows = db.execute(
        text("""
            SELECT trade_date, open, high, low, close, volume, value
            FROM ohlcv_daily WHERE code=:code
            ORDER BY trade_date DESC LIMIT :n
        """), {"code": code, "n": days}
    ).fetchall()
    data = [{"date":str(r[0]),"open":r[1],"high":r[2],"low":r[3],
             "close":r[4],"volume":r[5],"value":r[6]} for r in reversed(rows)]
    meta = db.execute(
        text("SELECT name FROM stock_meta WHERE code=:code LIMIT 1"),
        {"code": code}
    ).fetchone()
    return {"code": code, "name": (meta[0] if meta and meta[0] else code), "data": data}


@app.get("/api/stock/{code}/scores")
def api_scores(code: str, days: int = 30, db: Session = Depends(get_db)):
    """個股分數歷史"""
    rows = db.execute(
        text("""
            SELECT score_date, fundamental_score, valuation_score, chip_score,
                   momentum_score, macro_score, news_score, composite_score, signal,
                   candidate_score, entry_score, risk_score, final_score, final_action,
                   volume_score
            FROM daily_scores WHERE code=:code
            ORDER BY score_date DESC LIMIT :n
        """), {"code": code, "n": days}
    ).fetchall()
    keys = ["date","fundamental","valuation","chip","momentum","macro","news","composite","signal","candidate","entry","risk","final","final_action","volume"]
    return {"code": code, "scores": [dict(zip(keys, r)) for r in reversed(rows)]}


@app.get("/api/stock/{code}/latest_score")
def api_latest_score(code: str, db: Session = Depends(get_db)):
    """個股最新分數（雷達圖用）"""
    row = db.execute(
        text("""
            SELECT fundamental_score, valuation_score, chip_score,
                   momentum_score, macro_score, news_score, composite_score, signal,
                   volume_score, candidate_score, entry_score, risk_score,
                   risk_flags, final_score, final_action, core_score, stock_class
            FROM daily_scores WHERE code=:code
            ORDER BY score_date DESC LIMIT 1
        """), {"code": code}
    ).fetchone()
    if not row:
        raise HTTPException(404, "無分數資料")
    meta = db.execute(
        text("SELECT name FROM stock_meta WHERE code=:code LIMIT 1"),
        {"code": code}
    ).fetchone()
    # 量能分數：今日量 vs 20日均量，結合漲跌方向
    vol_row = db.execute(text("""
        SELECT o.volume, o.close, o.open,
               (SELECT AVG(v2.volume) FROM ohlcv_daily v2
                WHERE v2.code=:code AND v2.trade_date <= o.trade_date
                LIMIT 20) as avg_vol
        FROM ohlcv_daily o WHERE o.code=:code
        ORDER BY o.trade_date DESC LIMIT 1
    """), {"code": code}).fetchone()
    volume_score = 50.0
    if vol_row and vol_row[3] and vol_row[3] > 0:
        ratio = float(vol_row[0] or 0) / float(vol_row[3])
        chg = (float(vol_row[1] or 0) - float(vol_row[2] or 0)) / float(vol_row[2] or 1)
        if ratio >= 2 and chg > 0:   volume_score = min(95, 50 + ratio * 15)
        elif ratio >= 1.5 and chg > 0: volume_score = min(80, 50 + ratio * 10)
        elif ratio >= 2 and chg < 0: volume_score = max(15, 50 - ratio * 12)
        elif ratio >= 0.8:           volume_score = 50
        else:                        volume_score = max(20, 50 - (1-ratio) * 30)
    import json as _json
    return {
        "code": code, "name": (meta[0] if meta and meta[0] else code),
        "fundamental": row[0], "valuation": row[1], "chip": row[2],
        "momentum": row[3], "macro": row[4], "news": row[5],
        "composite": row[6], "signal": row[7],
        "volume_score":    round(float(row[8] or 50), 2),
        "candidate_score": round(float(row[9] or row[6] or 0), 2),
        "entry_score":     round(float(row[10] or 50), 2),
        "risk_score":      round(float(row[11] or 30), 2),
        "risk_flags":      _json.loads(row[12]) if row[12] else [],
        "final_score":     round(float(row[13] or row[6] or 0), 2),
        "final_action":    row[14] or row[7],
        "core_score":      round(float(row[15] or row[6] or 0), 2),
        "stock_class":     row[16] or "NORMAL",
    }



@app.post("/api/admin/update_latest")
def api_admin_update_latest(trade_date: Optional[str] = None):
    """
    一鍵更新最新資料：
    1. 夜盤 / 美股因子
    2. 日 K + 法人資料
    3. daily_scores 分數
    """
    from backend.services.latest_update import run_latest_update
    return run_latest_update(trade_date)


@app.get("/api/screener")
def api_screener(
    min_composite: float = 60,
    signal: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """選股篩選器"""
    cond = "composite_score >= :mc"
    params: dict = {"mc": min_composite, "n": limit}
    if signal:
        cond += " AND signal=:sig"
        params["sig"] = signal
    rows = db.execute(
        text(f"""
            SELECT ds.code, sm.name, ds.composite_score, ds.signal, o.close, o.change_pct
            FROM daily_scores ds
            LEFT JOIN stock_meta sm ON sm.code = ds.code
            LEFT JOIN ohlcv_daily o ON ds.code=o.code
            WHERE ds.score_date=(SELECT MAX(score_date) FROM daily_scores)
              AND o.trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
              AND {cond}
            ORDER BY ds.composite_score DESC LIMIT :n
        """), params
    ).fetchall()
    return [{"code":r[0],"name":r[1] or r[0],"composite":r[2],"signal":r[3],"close":r[4],"change_pct":r[5]}
            for r in rows]




@app.get("/api/market/context/latest")
def api_market_context_latest(db: Session = Depends(get_db)):
    """最新市場環境：隔日偏向、夜盤/美股 proxy、主線題材、量價廣度。"""
    row = db.execute(
        text("""
            SELECT context_date, market_bias_score, next_day_bias, trend_regime,
                   up_count, down_count, flat_count, up_ratio,
                   avg_change_pct, median_change_pct, total_value, market_volume_ratio,
                   avg_open_to_close_pct, avg_close_position,
                   breadth_score, volume_score, overnight_score,
                   nasdaq_ret, sox_ret, qqq_ret, sp500_ret, tw_futures_ret,
                   top_theme, top_theme_score, ai_theme_score, summary
            FROM market_context_daily
            ORDER BY context_date DESC LIMIT 1
        """)
    ).fetchone()
    if not row:
        return {
            "available": False,
            "summary": "尚未建立市場環境資料，請先跑 python -m scripts.update_market_context",
        }
    keys = [
        "context_date", "market_bias_score", "next_day_bias", "trend_regime",
        "up_count", "down_count", "flat_count", "up_ratio",
        "avg_change_pct", "median_change_pct", "total_value", "market_volume_ratio",
        "avg_open_to_close_pct", "avg_close_position",
        "breadth_score", "volume_score", "overnight_score",
        "nasdaq_ret", "sox_ret", "qqq_ret", "sp500_ret", "tw_futures_ret",
        "top_theme", "top_theme_score", "ai_theme_score", "summary",
    ]
    d = dict(zip(keys, row))
    d["available"] = True
    d["context_date"] = str(d["context_date"])
    return d


@app.get("/api/market/themes/latest")
def api_market_themes_latest(db: Session = Depends(get_db)):
    """最新主線題材排行。"""
    latest = db.execute(text("SELECT MAX(context_date) FROM theme_trend_daily")).scalar()
    if not latest:
        return []
    rows = db.execute(
        text("""
            SELECT theme, score, momentum_score, breadth_score, volume_ratio,
                   code_count, leader_codes, keyword_hits, summary
            FROM theme_trend_daily
            WHERE context_date=:d
            ORDER BY score DESC
        """),
        {"d": latest},
    ).fetchall()
    import json
    out = []
    for r in rows:
        try:
            leaders = json.loads(r[6] or "[]")
        except Exception:
            leaders = []
        out.append({
            "context_date": str(latest),
            "theme": r[0],
            "score": r[1],
            "momentum_score": r[2],
            "breadth_score": r[3],
            "volume_ratio": r[4],
            "code_count": r[5],
            "leader_codes": leaders,
            "keyword_hits": r[7],
            "summary": r[8],
        })
    return out


# ══════════════════════════════════════════════
# API: 策略帳戶
# ══════════════════════════════════════════════

class AccountCreate(BaseModel):
    name: str
    description: str = ""
    strategy_class: str = "MomentumBreakout"
    strategy_type: str = "rule_based"
    params: dict = {}
    weights: dict = {}
    initial_cash: float = 1_000_000


@app.get("/api/strategies")
def api_list_strategies(db: Session = Depends(get_db)):
    accs = db.query(StrategyAccount).all()
    return [PaperAccount(a.id).get_summary() for a in accs]


@app.post("/api/strategies")
def api_create_strategy(body: AccountCreate, db: Session = Depends(get_db)):
    if body.strategy_class not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"未知策略: {body.strategy_class}")
    acc = StrategyAccount(
        name=body.name, description=body.description,
        strategy_class=body.strategy_class, strategy_type=body.strategy_type,
        params=body.params, weights=body.weights,
        initial_cash=body.initial_cash, cash=body.initial_cash,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=settings.COMPETITION_DAYS),
    )
    db.add(acc); db.commit(); db.refresh(acc)
    return {"id": acc.id, "name": acc.name}


@app.get("/api/strategies/{account_id}")
def api_get_strategy(account_id: int, db: Session = Depends(get_db)):
    acc = db.query(StrategyAccount).filter_by(id=account_id).first()
    if not acc:
        raise HTTPException(404)
    return PaperAccount(account_id).get_summary()


@app.get("/api/strategies/{account_id}/positions")
def api_positions(account_id: int, db: Session = Depends(get_db)):
    rows = db.query(Position).filter_by(account_id=account_id).all()
    return [{"code":p.code,"lots":p.lots,"avg_cost":p.avg_cost} for p in rows]


@app.get("/api/strategies/{account_id}/trades")
def api_trades(account_id: int, limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(TradeLog).filter_by(account_id=account_id)\
             .order_by(TradeLog.ts.desc()).limit(limit).all()
    return [{"code":t.code,"direction":t.direction,"lots":t.lots,
             "price":t.price,"pnl":t.pnl,"trigger":t.trigger,
             "date":str(t.trade_date)} for t in rows]


@app.get("/api/strategies/{account_id}/equity")
def api_equity_curve(account_id: int, db: Session = Depends(get_db)):
    from backend.models.database import EquityCurve
    rows = db.query(EquityCurve).filter_by(account_id=account_id)\
             .order_by(EquityCurve.snap_date).all()
    return [{"date":str(r.snap_date),"total":r.total_equity,
             "cash":r.cash,"mktval":r.market_value,"ret":r.daily_return}
            for r in rows]


@app.delete("/api/strategies/{account_id}")
def api_delete_strategy(account_id: int, db: Session = Depends(get_db)):
    acc = db.query(StrategyAccount).filter_by(id=account_id).first()
    if not acc:
        raise HTTPException(404)
    db.delete(acc); db.commit()
    return {"deleted": account_id}


# ── 手動下單（測試 / 手動介入）────────────────

class ManualOrder(BaseModel):
    code: str
    direction: str   # BUY / SELL
    lots: int
    price: float

@app.post("/api/strategies/{account_id}/order")
def api_manual_order(account_id: int, body: ManualOrder):
    broker = PaperAccount(account_id)
    if body.direction == "BUY":
        r = broker.buy(body.code, body.lots, body.price, "手動下單")
    else:
        r = broker.sell(body.code, body.lots, body.price, "手動賣出")
    if not r.ok:
        raise HTTPException(400, r.msg)
    return {"ok": True, "msg": r.msg}


# ══════════════════════════════════════════════
# API: 競賽排行
# ══════════════════════════════════════════════

@app.get("/api/competition/ranking")
def api_competition(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    s = date.fromisoformat(start_date) if start_date else date.today() - timedelta(days=30)
    e = date.fromisoformat(end_date)   if end_date   else date.today()
    return get_competition_ranking(s, e)


@app.get("/api/competition/equity_curves")
def api_all_equity_curves(db: Session = Depends(get_db)):
    """所有帳戶的權益曲線（競賽圖）"""
    from backend.models.database import EquityCurve
    accs = db.query(StrategyAccount).all()
    result = []
    for acc in accs:
        rows = db.query(EquityCurve).filter_by(account_id=acc.id)\
                 .order_by(EquityCurve.snap_date).all()
        result.append({
            "account_id": acc.id,
            "name": acc.name,
            "strategy_class": acc.strategy_class,
            "curve": [{"date":str(r.snap_date),"total":r.total_equity} for r in rows]
        })
    return result


# ── 可用策略清單 ─────────────────────────────

@app.get("/api/strategy_registry")
def api_strategy_registry():
    return [{"name": s.__name__} for s in []]


# ── V2 P4: 美股夜盤 API ──────────────────────────────
@app.get("/api/v2/overnight")
async def api_v2_overnight(refresh: bool = False):
    try:
        from backend.collectors.overnight_market import get_overnight_summary
        return get_overnight_summary(force_refresh=refresh)
    except Exception as e:
        return {"error": str(e), "bias": {"overall": "無資料", "score": 50}}

# V2 P1: Sparkline 批次 API
@app.get("/api/v2/sparks")
async def api_v2_sparks(codes: str = ""):
    from backend.models.database import SessionLocal
    code_list = [c.strip() for c in codes.split(",") if c.strip()][:40]
    if not code_list:
        return {}
    db = SessionLocal()
    result = {}
    try:
        for code in code_list:
            rows = db.execute(text(
                "SELECT close FROM ohlcv_daily WHERE code=:c ORDER BY trade_date DESC LIMIT 20"
            ), {"c": code}).fetchall()
            if rows:
                result[code] = [float(r[0]) for r in reversed(rows)]
    finally:
        db.close()
    return result

# V2 P1: 警示系統 API
@app.get("/api/v2/alerts")
async def api_v2_alerts():
    from backend.models.database import SessionLocal
    db = SessionLocal()
    alerts = []
    try:
        latest = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()
        if not latest:
            return []
        rows = db.execute(text("""
            SELECT o.code, o.close, o.high, o.low, o.volume, o.change_pct,
                   o.open, ds.composite_score, ds.momentum_score
            FROM ohlcv_daily o
            LEFT JOIN daily_scores ds ON ds.code=o.code
                AND ds.score_date=(SELECT MAX(score_date) FROM daily_scores WHERE score_date<=o.trade_date)
            WHERE o.trade_date=:d
        """), {"d": latest}).fetchall()

        for r in rows:
            code,close,high,low,vol,chg,opn,comp,mom = r
            name = code
            if not close: continue
            hist = db.execute(text(
                "SELECT high,low,volume FROM ohlcv_daily WHERE code=:c AND trade_date<:d ORDER BY trade_date DESC LIMIT 20"
            ),{"c":code,"d":latest}).fetchall()
            if len(hist)<10: continue
            hh=[x[0] for x in hist if x[0]]; hl=[x[1] for x in hist if x[1]]; hv=[x[2] for x in hist if x[2]]
            if not hh: continue
            max20h=max(hh); min20l=min(hl); avg20v=sum(hv)/len(hv) if hv else 0
            nm=name or code
            if high and high>max20h:
                alerts.append({"id":f"{latest}_{code}_hi","type":"breakout_high",
                    "title":f"🚀 {nm}({code}) 突破20日高","body":f"今高{high:.1f} > 20日高{max20h:.1f}",
                    "code":code,"date":str(latest),"score":comp or 0})
            if low and low<min20l:
                alerts.append({"id":f"{latest}_{code}_lo","type":"breakout_low",
                    "title":f"⚠️ {nm}({code}) 跌破20日低","body":f"今低{low:.1f} < 20日低{min20l:.1f}",
                    "code":code,"date":str(latest),"score":comp or 0})
            if vol and avg20v and vol>avg20v*2.5:
                alerts.append({"id":f"{latest}_{code}_vol","type":"volume_spike",
                    "title":f"📊 {nm}({code}) 爆量","body":f"今量{vol/1e4:.0f}萬 = {vol/avg20v:.1f}x均量",
                    "code":code,"date":str(latest),"score":comp or 0})
    finally:
        db.close()
    alerts.sort(key=lambda x:-x["score"])
    return alerts[:60]

# ── P3 績效報表 ──────────────────────────────────────────
@app.get("/api/alerts/latest")
def api_latest_alerts(
    days: int = 20,
    volume_multiple: float = 2.0,
    limit: int = 60,
    as_of: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from backend.analytics.alerts import get_latest_alerts

    return get_latest_alerts(
        db=db,
        days=days,
        volume_multiple=volume_multiple,
        limit=limit,
        as_of=as_of,
    )
async def api_strategy_metrics(account_id: int):
    from backend.models.database import SessionLocal
    db = SessionLocal()
    try:
        # 已實現損益（SELL 記錄的 pnl）
        sells = db.execute(text("""
            SELECT pnl, price, lots, code, trade_date
            FROM trade_logs WHERE account_id=:a AND direction='SELL' AND pnl IS NOT NULL
        """), {"a": account_id}).fetchall()

        realized_pnl = sum(r[0] for r in sells)
        wins  = [r[0] for r in sells if r[0] > 0]
        loses = [r[0] for r in sells if r[0] < 0]
        trade_count = len(sells)
        win_rate    = round(len(wins) / trade_count * 100, 1) if trade_count else 0
        avg_profit  = round(sum(wins)  / len(wins),  0) if wins  else 0
        avg_loss    = round(sum(loses) / len(loses), 0) if loses else 0
        profit_factor = round(sum(wins) / abs(sum(loses)), 2) if loses and sum(loses) != 0 else None

        # 未實現損益（用最新收盤價 - 均成本）
        latest_date = db.execute(text("SELECT MAX(trade_date) FROM ohlcv_daily")).scalar()
        pos_rows = db.execute(text("""
            SELECT p.code, p.lots, p.avg_cost,
                   COALESCE(o.close, p.avg_cost) as close
            FROM positions p
            LEFT JOIN ohlcv_daily o ON o.code=p.code AND o.trade_date=:d
            WHERE p.account_id=:a
        """), {"a": account_id, "d": latest_date}).fetchall()

        unrealized_pnl = sum((r[3] - r[2]) * r[1] for r in pos_rows)

        # 帳戶資訊
        eq_last = db.execute(text(
            "SELECT total_equity FROM equity_curve WHERE account_id=:a ORDER BY snap_date DESC LIMIT 1"
        ), {"a": account_id}).fetchone()
        initial_cash = 200000
        total_equity_now = eq_last[0] if eq_last else initial_cash
        total_return_pct = round((total_equity_now - initial_cash) / initial_cash * 100, 2)

        # 最大回撤（從 equity_curve）
        eq_rows = db.execute(text("""
            SELECT total_equity FROM equity_curve WHERE account_id=:a ORDER BY snap_date
        """), {"a": account_id}).fetchall()
        max_drawdown = 0.0
        if eq_rows:
            peak = eq_rows[0][0]
            for r in eq_rows:
                if r[0] > peak: peak = r[0]
                dd = (peak - r[0]) / peak * 100 if peak else 0
                if dd > max_drawdown: max_drawdown = dd

        # 平均持倉天數（用 BUY 配對 SELL 同一檔）
        buys_raw = db.execute(text("""
            SELECT code, trade_date FROM trade_logs
            WHERE account_id=:a AND direction='BUY' ORDER BY trade_date
        """), {"a": account_id}).fetchall()
        from collections import defaultdict
        from datetime import datetime
        buy_q = defaultdict(list)
        for code, td in buys_raw:
            buy_q[code].append(td)
        holding_days = []
        for pnl, price, lots, code, sell_date in sells:
            if buy_q[code]:
                buy_date = buy_q[code].pop(0)
                try:
                    d1 = datetime.strptime(str(sell_date), "%Y-%m-%d")
                    d2 = datetime.strptime(str(buy_date), "%Y-%m-%d")
                    holding_days.append((d1 - d2).days)
                except: pass
        avg_holding_days = round(sum(holding_days) / len(holding_days), 1) if holding_days else 0

        return {
            "account_id": account_id,
            "total_return_pct": total_return_pct,
            "realized_pnl": round(realized_pnl, 0),
            "unrealized_pnl": round(unrealized_pnl, 0),
            "win_rate": win_rate,
            "trade_count": trade_count,
            "avg_holding_days": avg_holding_days,
            "max_drawdown": round(max_drawdown, 2),
            "avg_profit": avg_profit,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
        }
    finally:
        db.close()

@app.get("/api/strategies/{account_id}/metrics")
async def api_strategy_metrics(account_id: int):
    from backend.analytics.performance_metrics import calc_metrics
    from backend.models.database import SessionLocal
    db = SessionLocal()
    try:
        return calc_metrics(account_id, db)
    finally:
        db.close()

@app.get("/api/stock/{code}/technical")
def api_stock_technical(code: str, days: int = 60, db: Session = Depends(get_db)):
    """個股技術指標：MA5/MA20/Bollinger/成交量"""
    import statistics
    rows = db.execute(text("""
        SELECT trade_date, open, high, low, close, volume
        FROM ohlcv_daily WHERE code=:c
        ORDER BY trade_date DESC LIMIT :n
    """), {"c": code, "n": days + 25}).fetchall()
    if not rows:
        return {"code": code, "error": "無資料", "data": []}
    rows = list(reversed(rows))
    closes  = [r[4] for r in rows]
    volumes = [r[5] for r in rows]

    def ma(arr, n, i):
        if i < n - 1: return None
        return round(sum(arr[i-n+1:i+1]) / n, 2)

    def boll(closes, i, n=20):
        if i < n - 1: return None, None, None
        window = closes[i-n+1:i+1]
        mid = sum(window) / n
        std = statistics.stdev(window)
        return round(mid, 2), round(mid + 2*std, 2), round(mid - 2*std, 2)

    result = []
    for i, r in enumerate(rows):
        m5  = ma(closes, 5, i)
        m20 = ma(closes, 20, i)
        mid, upper, lower = boll(closes, i)
        vol_ma20 = ma(volumes, 20, i)
        vol_ratio = round(r[5] / vol_ma20, 2) if vol_ma20 else None
        result.append({
            "date": str(r[0]), "open": r[1], "high": r[2],
            "low": r[3], "close": r[4], "volume": r[5],
            "ma5": m5, "ma20": m20,
            "boll_mid": mid, "boll_upper": upper, "boll_lower": lower,
            "volume_ma20": vol_ma20, "volume_ratio": vol_ratio
        })
    return {"code": code, "data": result[-days:]}

@app.get("/api/stock/{code}/valuation")
def api_stock_valuation(code: str, db: Session = Depends(get_db)):
    """PE/PB 近一年歷史百分位"""
    rows = db.execute(text("""
        SELECT valuation_date, pe, pb, close, dividend_yield
        FROM valuation_daily
        WHERE code=:c AND valuation_date >= date('now','-365 days')
        ORDER BY valuation_date ASC
    """), {"c": code}).fetchall()
    if not rows:
        return {"code": code, "error": "無估值資料", "pe_percentile": None, "pb_percentile": None}

    pes = [r[1] for r in rows if r[1] and r[1] > 0]
    pbs = [r[2] for r in rows if r[2] and r[2] > 0]
    latest = rows[-1]

    def percentile(arr, val):
        if not arr or val is None or val <= 0: return None
        return round(sum(1 for x in arr if x <= val) / len(arr) * 100, 1)

    pe_pct = percentile(pes, latest[1])
    pb_pct = percentile(pbs, latest[2])

    return {
        "code": code,
        "valuation_date": str(latest[0]),
        "pe": latest[1],
        "pb": latest[2],
        "close": latest[3],
        "dividend_yield": latest[4],
        "pe_percentile": pe_pct,
        "pb_percentile": pb_pct,
        "pe_count": len(pes),
        "pb_count": len(pbs),
        "pe_note": f"目前 PE 位於近一年第 {pe_pct}%" if pe_pct else "PE 資料不足",
        "pb_note": f"目前 PB 位於近一年第 {pb_pct}%" if pb_pct else "PB 資料不足",
    }

@app.get("/api/stock/{code}/chip")
def api_stock_chip(code: str, db: Session = Depends(get_db)):
    """籌碼分析：法人連買天數、近5日累計、近20日趨勢"""
    rows = db.execute(text("""
        SELECT trade_date, foreign_net, trust_net, dealer_net
        FROM chip_daily WHERE code=:c
        ORDER BY trade_date DESC LIMIT 25
    """), {"c": code}).fetchall()
    if not rows:
        return {"code": code, "error": "無籌碼資料"}

    rows = list(reversed(rows))

    def consec_buy(series):
        days = 0
        for v in reversed(series):
            if v and v > 0: days += 1
            else: break
        return days

    def consec_sell(series):
        days = 0
        for v in reversed(series):
            if v and v < 0: days += 1
            else: break
        return days

    foreign = [r[1] or 0 for r in rows]
    trust   = [r[2] or 0 for r in rows]
    dealer  = [r[3] or 0 for r in rows]

    latest = rows[-1]
    return {
        "code": code,
        "trade_date": str(latest[0]),
        "foreign_net": latest[1],
        "trust_net": latest[2],
        "dealer_net": latest[3],
        "foreign_consec_buy":  consec_buy(foreign),
        "foreign_consec_sell": consec_sell(foreign),
        "trust_consec_buy":    consec_buy(trust),
        "trust_consec_sell":   consec_sell(trust),
        "foreign_5d": round(sum(foreign[-5:]), 0),
        "trust_5d":   round(sum(trust[-5:]), 0),
        "dealer_5d":  round(sum(dealer[-5:]), 0),
        "foreign_20d": round(sum(foreign[-20:]), 0),
        "trust_20d":   round(sum(trust[-20:]), 0),
        "chip_summary": (
            f"投信連買{consec_buy(trust)}天" if consec_buy(trust) >= 2 else
            f"投信連賣{consec_sell(trust)}天" if consec_sell(trust) >= 2 else
            f"投信近5日{'買超' if sum(trust[-5:])>0 else '賣超'}{abs(round(sum(trust[-5:]),0)):.0f}張"
        )
    }

@app.get("/api/stock/{code}/fundamental")
def api_stock_fundamental(code: str, db: Session = Depends(get_db)):
    """基本面分析：用月營收計算 YoY/MoM 趨勢與分數"""
    rows = db.execute(text("""
        SELECT year, month, revenue, mom_pct, yoy_pct, accumulated, published_date
        FROM monthly_revenue WHERE code=:c
        ORDER BY year DESC, month DESC LIMIT 12
    """), {"c": code}).fetchall()

    if not rows:
        return {"code": code, "error": "無月營收資料", "fundamental_score": 50,
                "missing_data_flags": ["monthly_revenue"]}

    latest = rows[0]
    yoy = latest[4]
    mom = latest[3]

    # 計算近3個月 YoY 平均
    recent_yoys = [r[4] for r in rows[:3] if r[4] is not None]
    avg_yoy = sum(recent_yoys) / len(recent_yoys) if recent_yoys else 0

    # 分數邏輯：YoY > 20% → 80+, YoY > 0% → 60+, YoY < -10% → 40-
    def score_from_yoy(y):
        if y is None: return 50
        if y >= 30:  return min(95, 75 + y * 0.3)
        if y >= 15:  return 70 + (y - 15) * 0.5
        if y >= 0:   return 60 + y * 0.67
        if y >= -10: return 50 + y * 1.0
        return max(20, 40 + y * 0.5)

    score = round(score_from_yoy(avg_yoy), 1)
    # MoM 微調
    if mom and mom > 5:  score = min(95, score + 3)
    if mom and mom < -5: score = max(20, score - 3)

    summary_parts = []
    if yoy is not None:
        summary_parts.append(f"營收YoY {yoy:+.1f}%")
    if mom is not None:
        summary_parts.append(f"MoM {mom:+.1f}%")
    summary_parts.append(f"基本面分數 {score}")

    return {
        "code": code,
        "latest_year": latest[0],
        "latest_month": latest[1],
        "revenue": latest[2],
        "yoy_pct": yoy,
        "mom_pct": mom,
        "avg_yoy_3m": round(avg_yoy, 2),
        "fundamental_score": score,
        "data_source": "monthly_revenue",
        "missing_data_flags": ["eps","roe","gross_margin"] if not rows else [],
        "summary": " | ".join(summary_parts),
        "published_date": str(latest[6]) if latest[6] else None
    }

@app.get("/api/market/regime")
def api_market_regime(db: Session = Depends(get_db)):
    """市場 regime：結合夜盤、大盤廣度、趨勢判斷倉位乘數"""
    latest = get_latest_trade_date(db)

    # 大盤廣度
    stats = db.execute(text("""
        SELECT
          SUM(CASE WHEN change>0 THEN 1 ELSE 0 END),
          SUM(CASE WHEN change<0 THEN 1 ELSE 0 END),
          AVG(change_pct), SUM(value)
        FROM ohlcv_daily WHERE trade_date=:d
    """), {"d": latest}).fetchone()
    up, dn, avg_chg, total_val = (stats[0] or 0), (stats[1] or 0), (stats[2] or 0), (stats[3] or 0)
    total = up + dn + max(1, (up+dn)*0.05)
    up_ratio = round(up / total * 100, 1) if total > 0 else 50

    # overnight
    ctx = db.execute(text("""
        SELECT overnight_score, next_day_bias, nasdaq_ret, sox_ret, ai_theme_score
        FROM market_context_daily ORDER BY context_date DESC LIMIT 1
    """)).fetchone()
    overnight_score = float(ctx[0] or 50) if ctx else 50
    next_day_bias = ctx[1] if ctx else "中性"

    # 綜合判斷
    breadth_score = min(100, max(0, up_ratio * 1.2))
    combined = overnight_score * 0.4 + breadth_score * 0.6

    if combined >= 65:   regime = "bullish";   pos_mult = 1.0
    elif combined >= 50: regime = "neutral";   pos_mult = 0.8
    elif combined >= 38: regime = "cautious";  pos_mult = 0.6
    else:                regime = "bearish";   pos_mult = 0.4

    regime_zh = {"bullish":"偏多","neutral":"中性","cautious":"謹慎偏空","bearish":"偏空"}[regime]

    # 更新 market_context_daily
    db.execute(text("""
        INSERT INTO market_context_daily
          (context_date, up_count, down_count, up_ratio, avg_change_pct,
           total_value, breadth_score, overnight_score, next_day_bias,
           market_bias_score, trend_regime)
        VALUES (:d,:up,:dn,:ur,:ac,:tv,:bs,:os,:nb,:mb,:tr)
        ON CONFLICT(context_date) DO UPDATE SET
          up_count=excluded.up_count, down_count=excluded.down_count,
          up_ratio=excluded.up_ratio, avg_change_pct=excluded.avg_change_pct,
          breadth_score=excluded.breadth_score, market_bias_score=excluded.market_bias_score,
          trend_regime=excluded.trend_regime
    """), {"d":latest,"up":int(up),"dn":int(dn),"ur":up_ratio,"ac":round(avg_chg,3),
           "tv":total_val,"bs":round(breadth_score,1),"os":overnight_score,
           "nb":next_day_bias,"mb":round(combined,1),"tr":regime_zh})
    db.commit()

    return {
        "trade_date": latest,
        "regime": regime,
        "regime_zh": regime_zh,
        "combined_score": round(combined, 1),
        "breadth_score": round(breadth_score, 1),
        "overnight_score": overnight_score,
        "up_count": int(up), "down_count": int(dn), "up_ratio": up_ratio,
        "avg_change_pct": round(avg_chg, 3),
        "position_multiplier": pos_mult,
        "next_day_bias": next_day_bias,
        "explanation": f"大盤上漲{up}家({up_ratio}%)，夜盤分數{overnight_score}，綜合{combined:.0f}→{regime_zh}，建議倉位乘數{pos_mult}"
    }

@app.get("/api/market/themes")
def api_market_themes(db: Session = Depends(get_db)):
    """主線題材趨勢：從 theme_trend_daily 取最新資料"""
    latest = db.execute(text(
        "SELECT MAX(context_date) FROM theme_trend_daily"
    )).scalar()

    if not latest:
        return {"themes": [], "data_date": None, "note": "無題材資料"}

    rows = db.execute(text("""
        SELECT theme, score, momentum_score, breadth_score,
               code_count, leader_codes, summary
        FROM theme_trend_daily
        WHERE context_date=:d
        ORDER BY score DESC
    """), {"d": latest}).fetchall()

    return {
        "data_date": str(latest),
        "themes": [{"theme":r[0],"score":round(r[1],1),
                    "momentum":round(r[2] or 0,1),
                    "breadth":round(r[3] or 0,1),
                    "code_count":r[4],"leaders":(lambda x: __import__("json").loads(x) if x else [])((r[5] or "").strip())[:3],
                    "summary":r[6]} for r in rows]
    }

@app.get("/api/backtest/compare")
def api_backtest_compare(db: Session = Depends(get_db)):
    """策略 vs 0050 vs 大盤報酬率對比"""
    # 各策略 equity curve
    accs = db.execute(text("SELECT id, name FROM strategy_accounts ORDER BY id")).fetchall()
    eq_rows = db.execute(text("""
        SELECT account_id, snap_date, total_equity
        FROM equity_curve ORDER BY account_id, snap_date
    """)).fetchall()

    from collections import defaultdict
    eq_by_acc = defaultdict(list)
    for r in eq_rows:
        eq_by_acc[r[0]].append({"date": str(r[1]), "equity": float(r[2])})

    # 找最早共同起始日
    start_dates = [v[0]["date"] for v in eq_by_acc.values() if v]
    start = min(start_dates) if start_dates else "2026-01-01"

    # 0050：yfinance split-adjusted 價格
    try:
        import yfinance as yf
        df0 = yf.download("0050.TW", start=start, end="2026-06-01", auto_adjust=True, progress=False)
        if not df0.empty:
            closes = df0[("Close","0050.TW")].dropna() if ("Close","0050.TW") in df0.columns else df0["Close"].dropna()
            base0 = float(closes.iloc[0])
            norm_0050 = [{"date": str(d)[:10], "ret": round(float(v)/base0*100-100, 2)}
                         for d, v in closes.items()]
        else:
            norm_0050 = []
    except Exception as e:
        print("yfinance error:", e)
        norm_0050 = []

    # 大盤廣度：change_pct 累積（截斷異常值±5%）
    market_curve_raw = db.execute(text("""
        SELECT trade_date, AVG(change_pct) as avg_chg
        FROM ohlcv_daily WHERE trade_date >= :s
          AND change_pct BETWEEN -10 AND 10
        GROUP BY trade_date ORDER BY trade_date
    """), {"s": start}).fetchall()
    mcum, norm_market = 1.0, []
    for i, r in enumerate(market_curve_raw):
        if i == 0:
            norm_market.append({"date": str(r[0]), "ret": 0.0})
        else:
            pct = max(min(float(r[1] or 0), 5.0), -5.0)
            mcum *= (1 + pct/100.0)
            norm_market.append({"date": str(r[0]), "ret": round(mcum*100-100, 2)})

    def normalize(series, key="equity", base=200000):
        return [{"date": r["date"] if isinstance(r, dict) else str(r[0]),
                 "ret": round((r[key] if isinstance(r, dict) else r[1]) / base * 100 - 100, 2)}
                for r in series]

    series = []
    for acc_id, name in accs:
        rows = eq_by_acc.get(acc_id, [])
        if not rows: continue
        base = 200000
        series.append({"id": acc_id, "name": name,
                       "data": normalize(rows, "equity", base),
                       "final_ret": round(rows[-1]["equity"]/base*100-100, 2) if rows else 0})

    series.append({"id": 0, "name": "0050", "data": norm_0050,
                   "final_ret": norm_0050[-1]["ret"] if norm_0050 else 0})
    # 大盤(等權) 因 stale 資料問題暫時移除

    return {"start_date": start, "series": series,
            "summary": [{"name":s["name"],"final_ret":s["final_ret"]} for s in series]}


# ═══════════════════════════════════
# V3 APIs
# ═══════════════════════════════════

@app.get("/api/decisions/explanations")
def api_decisions_explanations(
    date: str = None, account_id: int = None, strategy_id: int = None,
    code: str = None, action: str = None, limit: int = 100
):
    """V3-FIX-1 決策理由查詢"""
    from backend.v3.decision_explanations import query_explanations
    return query_explanations(
        trade_date=date, account_id=account_id, strategy_id=strategy_id,
        code=code, action=action, limit=limit
    )


@app.get("/api/v3/strategies/router")
def api_strategies_router(query_date: str = None):
    """V3-FIX-2 策略路由器狀態"""
    from backend.v3.strategy_router import get_latest_router, compute_router
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    result = get_latest_router(td)
    if not result or "market_trend" not in result:
        result = compute_router(td)
    return result


@app.get("/api/risk/budget")
def api_risk_budget(account_id: int = None, date: str = None):
    """V3-FIX-3 風險預算狀態"""
    from backend.v3.risk_budget_manager import get_budget_status
    return get_budget_status(account_id=account_id, trade_date=date)


@app.get("/api/v3/strategies/leaderboard")
def api_strategies_leaderboard(query_date: str = None):
    """V3-FIX-6 策略排名"""
    from backend.v3.strategy_leaderboard import get_leaderboard, compute_leaderboard
    from datetime import date as ddate
    result = get_leaderboard(as_of_date=query_date)
    if not result:
        td = ddate.fromisoformat(query_date) if query_date else ddate.today()
        result = compute_leaderboard(td)
    return result


@app.get("/api/paper/research-log")
def api_paper_research_log(
    code: str = None, strategy_id: int = None,
    date_from: str = None, date_to: str = None, limit: int = 100
):
    """V3-FIX-7 Paper Trading Research Log"""
    from backend.v3.strategy_leaderboard import get_research_log, get_research_summary
    logs = get_research_log(code=code, strategy_id=strategy_id,
                            date_from=date_from, date_to=date_to, limit=limit)
    summary = get_research_summary(strategy_id=strategy_id)
    return {"logs": logs, "summary": summary}


# ═══════════════════════════════════
# V3b APIs (FIX-10~15)
# ═══════════════════════════════════

@app.get("/api/capital/config")
def api_capital_config():
    """V3-FIX-15 資金與風險設定"""
    try:
        from config.capital_config import CAPITAL_CONFIG
        return CAPITAL_CONFIG.summary()
    except Exception as e:
        return {"mode":"assistive","allow_auto_order":False,
                "require_user_confirmation":True,"error":str(e)}


@app.get("/api/candidates/trade-plans")
def api_candidate_trade_plans(
    query_date: str = None, code: str = None,
    candidate_pool_type: str = None, limit: int = 50
):
    """V3-FIX-11 候選股交易計畫"""
    from backend.v3.candidate_trade_plans import get_trade_plans, generate_daily_plans
    from datetime import date as ddate
    plans = get_trade_plans(plan_date=query_date, code=code, limit=limit)
    if not plans and not query_date:
        plans = generate_daily_plans(ddate.today(), limit=limit)
    return plans


@app.get("/api/watchlist/alerts")
def api_watchlist_alerts(
    alert_date: str = None, code: str = None, limit: int = 100
):
    """V3-FIX-13 看盤提醒"""
    from backend.v3.watchlist_alerts import get_alerts
    return get_alerts(alert_date=alert_date, code=code, limit=limit)


@app.get("/api/candidates/accuracy")
def api_candidates_accuracy(
    strategy_id: int = None, candidate_pool_type: str = None,
    code: str = None, start_date: str = None, end_date: str = None, limit: int = 100
):
    """V3-FIX-14 候選股勝率追蹤"""
    from backend.v3.watchlist_alerts import get_accuracy_list, get_accuracy_stats
    return {
        "stats": get_accuracy_stats(strategy_id=strategy_id,
                                    candidate_pool_type=candidate_pool_type,
                                    start_date=start_date, end_date=end_date),
        "records": get_accuracy_list(code=code, limit=limit),
    }


@app.get("/api/candidates/news")
def api_candidates_news(code: str = None, query_date: str = None, limit: int = 10):
    """V3-FIX-12 候選股新聞（框架）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        q = "SELECT * FROM candidate_news WHERE 1=1"
        params = {}
        if code: q += " AND code=:code"; params["code"] = code
        if query_date: q += " AND news_time<=:d"; params["d"] = query_date+" 23:59:59"
        q += " ORDER BY news_time DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(_text(q), params).fetchall()
        cols = ["id","news_time","code","name","title","source",
                "source_credibility_score","sentiment","related_themes",
                "is_official_disclosure","is_financial_report",
                "is_monthly_revenue","is_investor_conference","summary","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


# ═══════════════════════════════════
# V3c APIs (FIX-4, FIX-5, FIX-8)
# ═══════════════════════════════════

@app.get("/v3", response_class=HTMLResponse)
def page_v3_dashboard(request: Request):
    """V3-FIX-8 V3 系統總覽頁面"""
    return templates.TemplateResponse("v3_dashboard.html", {"request": request})


@app.get("/api/backtest/realistic/results")
def api_realistic_fills(
    account_id: int = None, code: str = None,
    start_date: str = None, limit: int = 100
):
    """V3-FIX-4 真實成交記錄"""
    from backend.v3.realistic_trade_fills import get_fills
    return get_fills(account_id=account_id, code=code,
                     start_date=start_date, limit=limit)


@app.post("/api/backtest/realistic/fill")
def api_process_fill(
    account_id: int, strategy_id: int, code: str, action: str,
    signal_date: str, requested_shares: float,
    signal_price: float = None, is_fractional: bool = False
):
    """V3-FIX-4 處理單筆成交請求"""
    from backend.v3.realistic_trade_fills import process_fill
    from datetime import date as ddate
    sd = ddate.fromisoformat(signal_date)
    return process_fill(account_id=account_id, strategy_id=strategy_id,
                        code=code, action=action, signal_date=sd,
                        requested_shares=requested_shares,
                        signal_price=signal_price, is_fractional=is_fractional)


@app.get("/api/backtest/walk-forward")
def api_walk_forward_results(strategy_id: int = None, limit: int = 200):
    """V3-FIX-5 Walk-forward 結果"""
    from backend.v3.walk_forward_validator import get_walk_forward_results
    return get_walk_forward_results(strategy_id=strategy_id, limit=limit)


@app.post("/api/backtest/walk-forward/run")
def api_run_walk_forward(
    strategy_id: int = None,
    data_start: str = "2025-02-01",
    data_end: str = None
):
    """V3-FIX-5 執行 Walk-forward 驗證"""
    from backend.v3.walk_forward_validator import run_walk_forward, run_all_strategies_walk_forward
    from datetime import date as ddate
    start = ddate.fromisoformat(data_start)
    end   = ddate.fromisoformat(data_end) if data_end else ddate.today()
    if strategy_id:
        results = run_walk_forward(strategy_id, start, end)
        return results
    else:
        return run_all_strategies_walk_forward(start, end)


# ═══════════════════════════════════
# V4 APIs
# ═══════════════════════════════════

@app.get("/api/quality/data")
def api_data_quality(query_date: str = None, limit: int = 50):
    """V4-1 資料品質檢查"""
    from backend.v4.data_quality import run_data_quality_checks, get_quality_report
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    existing = get_quality_report(str(td), limit)
    if not existing:
        result = run_data_quality_checks(td)
        existing = get_quality_report(str(td), limit)
    return {"checks": existing, "count": len(existing)}


@app.get("/api/workflow/daily-runs")
def api_workflow_runs(run_date: str = None, limit: int = 100):
    """V4-3 每日工作流程記錄"""
    from backend.v4.daily_workflow import get_workflow_runs
    return get_workflow_runs(run_date=run_date, limit=limit)


@app.post("/api/workflow/run")
def api_run_workflow(run_date: str = None):
    """V4-3 執行每日工作流程"""
    from backend.v4.daily_workflow import run_daily_workflow
    from datetime import date as ddate
    td = ddate.fromisoformat(run_date) if run_date else ddate.today()
    return run_daily_workflow(td)


@app.get("/api/trade-plan/tomorrow")
def api_tomorrow_trade_plan(query_date: str = None, limit: int = 30):
    """V4-4 明日交易計畫"""
    from backend.v3.candidate_trade_plans import get_trade_plans, generate_daily_plans
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    plans = get_trade_plans(plan_date=str(td), limit=limit)
    if not plans:
        plans = generate_daily_plans(td, limit=limit)
    return plans


@app.get("/api/v4/strategies/kill-switch")
def api_strategy_kill_switch(query_date: str = None):
    """V4-9 策略 Kill Switch 狀態"""
    from backend.v4.strategy_kill_switch import run_kill_switch, get_kill_switch_status
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    status = get_kill_switch_status(str(td))
    if not status:
        status = run_kill_switch(td)
    return status


@app.get("/api/market/classification")
def api_market_classification(
    code: str = None, primary_category: str = None,
    min_heat_score: float = None, limit: int = 200
):
    """V4-13 股市分類"""
    from backend.v4.market_sector import get_classification, build_classification
    from datetime import date as ddate
    result = get_classification(code=code, primary_category=primary_category,
                                min_heat=min_heat_score, limit=limit)
    if not result:
        build_classification(ddate.today())
        result = get_classification(code=code, primary_category=primary_category,
                                    min_heat=min_heat_score, limit=limit)
    return result


@app.get("/api/market/theme-exposure")
def api_theme_exposure(account_id: int = None):
    """V4-13 主題曝險"""
    from backend.v4.market_sector import get_theme_exposure
    return get_theme_exposure(account_id=account_id)


@app.get("/api/market/sector-heat")
def api_sector_heat():
    """V4-13 產業題材熱度"""
    from backend.v4.market_sector import get_classification
    from collections import defaultdict
    data = get_classification(limit=2000)
    heat_by_cat = defaultdict(list)
    for d in data:
        heat_by_cat[d["primary_category"]].append(d["theme_heat_score"] or 50)
    return [
        {"category": cat, "avg_heat": round(sum(v)/len(v), 1), "count": len(v)}
        for cat, v in sorted(heat_by_cat.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
    ]


@app.get("/api/intraday/watch")
def api_intraday_watch():
    """V4-8 盤中觀察（無分鐘資料時SKIPPED）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        count = db.execute(_text("SELECT COUNT(*) FROM ohlcv_1min")).scalar() or 0
        if count == 0:
            return {"status": "SKIPPED", "reason": "ohlcv_1min 無資料，盤中觀察跳過",
                    "events": []}
        rows = db.execute(_text("SELECT * FROM intraday_watch_events ORDER BY event_time DESC LIMIT 50")).fetchall()
        return {"status": "OK", "events": rows}
    except:
        return {"status": "SKIPPED", "reason": "ohlcv_1min 資料表不存在", "events": []}
    finally:
        db.close()


# ═══════════════════════════════════
# V4b APIs (Factor Store, Research, Stress Test)
# ═══════════════════════════════════

@app.get("/api/factors/store")
def api_factor_store(
    code: str = None, query_date: str = None,
    factor_group: str = None, factor_name: str = None,
    decision_time: str = None, limit: int = 200
):
    """V4-2 Factor Store"""
    from backend.v4.factor_store import get_factors, build_factor_store
    from datetime import date as ddate
    td = ddate.fromisoformat(query_date) if query_date else ddate.today()
    result = get_factors(code=code, factor_date=str(td),
                         factor_group=factor_group, factor_name=factor_name,
                         decision_time=decision_time, limit=limit)
    if not result and not code:
        build_factor_store(td)
        result = get_factors(code=code, factor_date=str(td),
                             factor_group=factor_group, factor_name=factor_name,
                             decision_time=decision_time, limit=limit)
    return result


@app.get("/api/research/backtest-paper-gap")
def api_backtest_paper_gap_v2(strategy_id: int = None, limit: int = 100):
    """V4-5 回測vs實測差距"""
    from backend.v4.research import get_gap_analysis, analyze_backtest_paper_gap
    from datetime import date as ddate
    result = get_gap_analysis(strategy_id=strategy_id, limit=limit)
    if not result:
        analyze_backtest_paper_gap(strategy_id=strategy_id, analysis_date=ddate.today())
        result = get_gap_analysis(strategy_id=strategy_id, limit=limit)
    return result


@app.get("/api/research/strategy-attribution")
def api_strategy_attribution_v2(strategy_id: int = None, limit: int = 100):
    """V4-6 策略獲利歸因"""
    from backend.v4.research import run_strategy_attribution
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _text
    from datetime import date as ddate
    db = SessionLocal()
    try:
        rows = db.execute(_text(
            "SELECT * FROM strategy_attribution" +
            (" WHERE strategy_id=:sid" if strategy_id else "") +
            " ORDER BY total_pnl DESC LIMIT :limit"
        ), {"sid": strategy_id, "limit": limit} if strategy_id else {"limit": limit}).fetchall()
        if not rows:
            run_strategy_attribution(strategy_id=strategy_id, analysis_date=ddate.today())
            rows = db.execute(_text(
                "SELECT * FROM strategy_attribution ORDER BY total_pnl DESC LIMIT :limit"
            ), {"limit": limit}).fetchall()
        cols = ["id","analysis_date","strategy_id","account_id","attribution_type",
                "attribution_key","realized_pnl","unrealized_pnl","total_pnl",
                "pnl_contribution_pct","trade_count","win_rate","avg_return",
                "max_drawdown","concentration_warning","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


@app.get("/api/portfolio/optimizer")
def api_portfolio_optimizer_v2(account_id: int = None):
    """V4-7 投組配置器"""
    from backend.v4.research import run_portfolio_optimizer
    from datetime import date as ddate
    return run_portfolio_optimizer(account_id=account_id, plan_date=ddate.today())


@app.get("/api/risk/scenario-stress")
def api_scenario_stress_v2(account_id: int = None, test_date: str = None):
    """V4-10 情境壓力測試"""
    from backend.v4.research import get_stress_results, run_scenario_stress_test
    from datetime import date as ddate
    td = ddate.fromisoformat(test_date) if test_date else ddate.today()
    result = get_stress_results(str(td))
    if not result:
        run_scenario_stress_test(account_id=account_id, test_date=td)
        result = get_stress_results(str(td))
    return result


@app.get("/api/reports/research")
def api_research_report(report_date: str = None):
    """V4-11 研究報告"""
    from backend.v4.research_report import export_research_report
    from datetime import date as ddate
    td = ddate.fromisoformat(report_date) if report_date else ddate.today()
    path = export_research_report(td)
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "content": content, "date": str(td)}
    except:
        return {"path": path, "error": "報告尚未生成"}


# ═══════════════════════════════════
# V5 APIs
# ═══════════════════════════════════

@app.get("/api/data-quality/technical")
def api_technical_coverage(trade_date: str = None):
    """技術指標覆蓋率"""
    from backend.services.technical_features import get_coverage_stats
    return get_coverage_stats(trade_date)


@app.get("/api/technical/{code}")
def api_technical_features(code: str, trade_date: str = None):
    """單股技術指標"""
    from backend.services.technical_features import get_technical_features
    result = get_technical_features(code, trade_date)
    return result or {"error": f"{code} 無技術指標資料"}


@app.get("/api/freshness")
def api_data_freshness():
    """資料新鮮度總覽"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        tables = {
            "ohlcv_daily":            "SELECT MAX(trade_date) FROM ohlcv_daily",
            "daily_scores":           "SELECT MAX(score_date) FROM daily_scores",
            "technical_features":     "SELECT MAX(trade_date) FROM technical_daily_features",
            "equity_curve":           "SELECT MAX(snap_date) FROM equity_curve",
            "chip_daily":             "SELECT MAX(trade_date) FROM chip_daily",
            "theme_trend":            "SELECT MAX(context_date) FROM theme_trend_daily",
            "market_context":         "SELECT MAX(context_date) FROM market_context_daily",
            "ohlcv_1min":             "SELECT MAX(date(ts)) FROM ohlcv_1min",
        }
        result = {}
        for name, q in tables.items():
            try:
                v = db.execute(_t(q)).scalar()
                result[name] = str(v) if v else "無資料"
            except:
                result[name] = "表不存在"
        return result
    finally:
        db.close()


@app.get("/api/strategy-decisions")
def api_strategy_decisions(
    account_id: int = None, signal_date: str = None,
    action: str = None, limit: int = 50
):
    """策略決策記錄"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        q = "SELECT * FROM strategy_decision_logs WHERE 1=1"
        params = {}
        if account_id: q += " AND account_id=:aid"; params["aid"] = account_id
        if signal_date: q += " AND signal_date=:sd"; params["sd"] = signal_date
        if action: q += " AND action=:action"; params["action"] = action
        q += " ORDER BY id DESC LIMIT :limit"; params["limit"] = limit
        rows = db.execute(_t(q), params).fetchall()
        cols = ["id","account_id","strategy_name","mode","signal_date",
                "data_cutoff_time","execution_date","execution_time_model",
                "code","action","candidate_score","technical_score","chip_score",
                "fundamental_score","risk_score","final_score","suggested_shares",
                "reference_price","expected_fill_price","stop_loss","target_price",
                "is_blocked","blocked_reason","reason_summary","no_lookahead_pass","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()

@app.get("/api/daily-review")
def api_daily_review(signal_date: str = None, review_date: str = None):
    """每日選股檢討書"""
    from backend.services.daily_review import generate_daily_review
    from datetime import date as ddate, timedelta
    today = ddate.today()
    sig = ddate.fromisoformat(signal_date) if signal_date else today - timedelta(days=1)
    rev = ddate.fromisoformat(review_date) if review_date else today
    path = generate_daily_review(sig, rev)
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return {"signal_date": str(sig), "review_date": str(rev), "path": path, "content": f.read()}
        except Exception as e:
            return {"signal_date": str(sig), "error": str(e)}
    return {"signal_date": str(sig), "content": "尚無檢討書，請先執行每日工作流程"}


@app.get("/api/daily-review-latest")
def api_daily_review_latest():
    """自動找最新有資料的每日檢討書"""
    from backend.services.daily_review import generate_daily_review
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    db = SessionLocal()
    try:
        # 找最新 plan_date + 其下一個交易日
        dates = db.execute(_t("""
            SELECT DISTINCT plan_date FROM candidate_trade_plans
            ORDER BY plan_date DESC LIMIT 5
        """)).fetchall()
        for (plan_date,) in dates:
            # 找 plan_date 之後有 ohlcv 的最近日期
            next_day = db.execute(_t("""
                SELECT MIN(trade_date) FROM ohlcv_daily
                WHERE trade_date > :d
            """), {"d": plan_date}).scalar()
            if next_day:
                from datetime import date as dd
                sig = dd.fromisoformat(str(plan_date))
                rev = dd.fromisoformat(str(next_day))
                path = generate_daily_review(sig, rev)
                if path:
                    with open(path, encoding="utf-8") as f:
                        return {"signal_date": str(sig), "review_date": str(rev),
                                "path": path, "content": f.read()}
        return {"signal_date": None, "content": "尚無可供檢討的資料"}
    finally:
        db.close()

@app.get("/api/daily-review-history")
def api_daily_review_history(limit: int = 30):
    """歷史檢討書清單"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from pathlib import Path
    db = SessionLocal()
    try:
        dates = db.execute(_t("""
            SELECT DISTINCT plan_date FROM candidate_trade_plans
            ORDER BY plan_date DESC LIMIT :n
        """), {"n": limit}).fetchall()
        results = []
        for (plan_date,) in dates:
            next_day = db.execute(_t("""
                SELECT MIN(trade_date) FROM ohlcv_daily WHERE trade_date > :d
            """), {"d": plan_date}).scalar()
            if not next_day: continue
            # 統計績效
            rows = db.execute(_t("""
                SELECT COUNT(*), AVG(change_pct),
                       SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END)
                FROM (
                    SELECT DISTINCT ctp.code, o.change_pct
                    FROM candidate_trade_plans ctp
                    LEFT JOIN ohlcv_daily o ON o.code=ctp.code AND o.trade_date=:rev
                    WHERE ctp.plan_date=:sig AND o.close IS NOT NULL
                )
            """), {"sig": plan_date, "rev": next_day}).fetchone()
            total = int(rows[0] or 0)
            avg_ret = round(float(rows[1] or 0), 2)
            win = int(rows[2] or 0)
            path = Path(f"data/reports/daily_review_{plan_date}.md")
            results.append({
                "signal_date": str(plan_date),
                "review_date": str(next_day),
                "total": total,
                "win": win,
                "win_rate": round(win/total*100, 1) if total else 0,
                "avg_return": avg_ret,
                "has_report": path.exists(),
            })
        return results
    finally:
        db.close()
