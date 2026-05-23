"""
backend/collectors/overnight_market.py
抓取美股夜盤資料，產生隔日台股市場偏向判斷。
使用 yfinance，免費，不需要 API key。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
import yfinance as yf
from loguru import logger

CACHE_FILE = Path("data/overnight_cache.json")
CACHE_FILE.parent.mkdir(exist_ok=True)

# 觀察標的
SYMBOLS = {
    "^GSPC": {"name": "S&P500",     "weight": 0.15, "sector": "broad"},
    "QQQ":   {"name": "NASDAQ 100",  "weight": 0.25, "sector": "tech"},
    "SOXX":  {"name": "費城半導體",    "weight": 0.30, "sector": "semi"},
    "TSM":   {"name": "台積電 ADR",   "weight": 0.20, "sector": "semi"},
    "NVDA":  {"name": "NVIDIA",      "weight": 0.10, "sector": "ai"},
}

def fetch_overnight() -> dict:
    """抓最近一個交易日的收盤資料，計算各指標漲跌幅。"""
    result = {}
    for sym, info in SYMBOLS.items():
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(period="3d")
            if len(hist) < 2:
                continue
            prev_close = float(hist["Close"].iloc[-2])
            last_close = float(hist["Close"].iloc[-1])
            ret = (last_close - prev_close) / prev_close if prev_close else 0
            result[sym] = {
                "name":   info["name"],
                "close":  round(last_close, 2),
                "ret":    round(ret * 100, 2),
                "sector": info["sector"],
                "weight": info["weight"],
            }
            logger.info(f"[OVERNIGHT] {sym} {ret:+.2%}")
        except Exception as e:
            logger.warning(f"[OVERNIGHT] {sym} 失敗: {e}")
    return result

def compute_bias(data: dict) -> dict:
    """根據各指標漲跌幅，計算明日台股各板塊偏向。"""
    if not data:
        return {"overall": "中性", "tech": "中性", "semi": "中性", "ai": "中性", "score": 50}

    # 分板塊加權平均漲跌
    sector_ret = {}
    sector_weight = {}
    for sym, d in data.items():
        s = d["sector"]
        w = d["weight"]
        sector_ret[s]    = sector_ret.get(s, 0) + d["ret"] * w
        sector_weight[s] = sector_weight.get(s, 0) + w

    def norm(s):
        return sector_ret.get(s, 0) / sector_weight.get(s, 1)

    def label(ret):
        if ret > 1.2:   return "強勢偏多"
        if ret > 0.4:   return "偏多"
        if ret > -0.4:  return "中性"
        if ret > -1.2:  return "偏空"
        return "強勢偏空"

    broad_ret = norm("broad")
    tech_ret  = norm("tech")
    semi_ret  = norm("semi")
    ai_ret    = norm("ai")
    overall_ret = broad_ret * 0.3 + tech_ret * 0.3 + semi_ret * 0.4

    # 0-100 分數（50=中性）
    score = min(100, max(0, 50 + overall_ret * 10))

    return {
        "overall": label(overall_ret),
        "tech":    label(tech_ret),
        "semi":    label(semi_ret),
        "ai":      label(ai_ret),
        "score":   round(score, 1),
        "overall_ret": round(overall_ret, 2),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

def get_overnight_summary(force_refresh=False) -> dict:
    """讀取快取或重新抓取，回傳完整摘要。"""
    # 快取 6 小時
    if not force_refresh and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
            cached_at = datetime.fromisoformat(cached.get("fetched_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=6):
                return cached
        except Exception:
            pass

    data   = fetch_overnight()
    bias   = compute_bias(data)
    result = {"symbols": data, "bias": bias, "fetched_at": datetime.now().isoformat()}
    save_to_db(data, bias)  # 寫入 market_context_daily
    try:
        CACHE_FILE.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result

if __name__ == "__main__":
    s = get_overnight_summary(force_refresh=True)
    print(json.dumps(s["bias"], ensure_ascii=False, indent=2))
    for sym, d in s["symbols"].items():
        print(f"  {sym:5} {d['name']:12} {d['ret']:+.2f}%")


def save_to_db(data: dict, bias: dict):
    """把夜盤結果寫進 market_context_daily.overnight_score 等欄位。"""
    try:
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        from datetime import date

        today = date.today().isoformat()
        db = SessionLocal()

        # 取各指標漲跌
        nasdaq_ret = data.get("QQQ", {}).get("ret", 0)
        sox_ret    = data.get("SOXX", {}).get("ret", 0)
        qqq_ret    = data.get("QQQ", {}).get("ret", 0)
        sp500_ret  = data.get("^GSPC", data.get("SPY", {})).get("ret", 0)
        ai_score   = min(100, max(0, 50 + data.get("NVDA", {}).get("ret", 0) * 8))

        # upsert market_context_daily
        existing = db.execute(text(
            "SELECT context_date FROM market_context_daily WHERE context_date=:d"
        ), {"d": today}).fetchone()

        if existing:
            db.execute(text("""
                UPDATE market_context_daily
                SET overnight_score=:os, nasdaq_ret=:nq, sox_ret=:sox,
                    qqq_ret=:qqq, sp500_ret=:sp, ai_theme_score=:ai
                WHERE context_date=:d
            """), {"os": bias["score"], "nq": nasdaq_ret, "sox": sox_ret,
                   "qqq": qqq_ret, "sp": sp500_ret, "ai": ai_score, "d": today})
        else:
            db.execute(text("""
                INSERT INTO market_context_daily
                  (context_date, market_bias_score, next_day_bias,
                   overnight_score, nasdaq_ret, sox_ret, qqq_ret, sp500_ret,
                   ai_theme_score, summary)
                VALUES (:d, :mbs, :ndb, :os, :nq, :sox, :qqq, :sp, :ai, :sum)
            """), {"d": today, "mbs": bias["score"], "ndb": bias["overall"],
                   "os": bias["score"], "nq": nasdaq_ret, "sox": sox_ret,
                   "qqq": qqq_ret, "sp": sp500_ret, "ai": ai_score,
                   "sum": f"美股夜盤 {bias['overall']} SOXX{sox_ret:+.1f}% NVDA{data.get('NVDA',{}).get('ret',0):+.1f}%"})

        db.commit()
        db.close()
        logger.info(f"[OVERNIGHT] 寫入 DB overnight_score={bias['score']} {bias['overall']}")
    except Exception as e:
        logger.warning(f"[OVERNIGHT] 寫入 DB 失敗: {e}")
