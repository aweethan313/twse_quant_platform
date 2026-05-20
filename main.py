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


@app.get("/api/market/overview")
def api_market_overview(db: Session = Depends(get_db)):
    """大盤概覽：今日漲跌家數、成交值"""
    today = date.today()
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
        "trade_date": str(today),
        "up": int(rows[0] or 0),
        "down": int(rows[1] or 0),
        "flat": int(rows[2] or 0),
        "total_value_bn": round((rows[3] or 0) / 1e5, 2),  # 億
    }


@app.get("/api/market/top_movers")
def api_top_movers(limit: int = 20, db: Session = Depends(get_db)):
    """今日漲跌幅排行"""
    today = date.today()
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
                   momentum_score, macro_score, news_score, composite_score, signal
            FROM daily_scores WHERE code=:code
            ORDER BY score_date DESC LIMIT :n
        """), {"code": code, "n": days}
    ).fetchall()
    keys = ["date","fundamental","valuation","chip","momentum","macro","news","composite","signal"]
    return {"code": code, "scores": [dict(zip(keys, r)) for r in reversed(rows)]}


@app.get("/api/stock/{code}/latest_score")
def api_latest_score(code: str, db: Session = Depends(get_db)):
    """個股最新分數（雷達圖用）"""
    row = db.execute(
        text("""
            SELECT fundamental_score, valuation_score, chip_score,
                   momentum_score, macro_score, news_score, composite_score, signal
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
    return {
        "code": code, "name": (meta[0] if meta and meta[0] else code),
        "fundamental": row[0], "valuation": row[1], "chip": row[2],
        "momentum": row[3], "macro": row[4], "news": row[5],
        "composite": row[6], "signal": row[7]
    }


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
@app.get("/api/strategies/{account_id}/metrics")
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
