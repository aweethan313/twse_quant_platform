"""
backend/collectors/overnight_market.py
抓取美股夜盤資料，產生隔日台股市場偏向判斷。
使用 yfinance，免費，不需要 API key。
加入：美光 MU、台灣加權指數 ^TWII、台指期 ^TWF
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
import yfinance as yf
from loguru import logger

CACHE_FILE = Path("data/overnight_cache.json")
CACHE_FILE.parent.mkdir(exist_ok=True)

# 觀察標的（加入 MU 美光 + 台股指數）
SYMBOLS = {
    "^GSPC": {"name": "S&P500",      "weight": 0.15, "sector": "broad"},
    "QQQ":   {"name": "NASDAQ 100",  "weight": 0.25, "sector": "tech"},
    "SOXX":  {"name": "費城半導體",   "weight": 0.30, "sector": "semi"},
    "TSM":   {"name": "台積電 ADR",  "weight": 0.20, "sector": "semi"},
    "NVDA":  {"name": "NVIDIA",      "weight": 0.10, "sector": "ai"},
    "MU":    {"name": "美光",        "weight": 0.10, "sector": "semi"},
}

# 台股相關（獨立追蹤，不計入偏向分數）
TW_SYMBOLS = {
    "^TWII": {"name": "台灣加權指數", "sector": "tw_index"},
    "^TWF":  {"name": "台指期",      "sector": "tw_futures"},
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

    # 抓台股指數（best effort）
    for sym, info in TW_SYMBOLS.items():
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(period="3d")
            if len(hist) < 1:
                continue
            last_close = float(hist["Close"].iloc[-1])
            # 確保只比較連續交易日（≤5天）
            prev_close = last_close
            point_change = 0.0
            ret = 0.0
            if len(hist) >= 2:
                last_date = hist.index[-1]
                prev_date = hist.index[-2]
                day_diff = abs((last_date - prev_date).days)
                if day_diff <= 5:
                    prev_close = float(hist["Close"].iloc[-2])
                    point_change = round(last_close - prev_close, 2)
                    ret = (last_close - prev_close) / prev_close if prev_close else 0
            result[sym] = {
                "name":         info["name"],
                "close":        round(last_close, 2),
                "ret":          round(ret * 100, 2),
                "point_change": round(point_change, 2),
                "sector":       info["sector"],
                "weight":       0,  # 不計入偏向
            }
            logger.info(f"[OVERNIGHT] {sym} {last_close:.0f} ({ret:+.2%})")
        except Exception as e:
            logger.warning(f"[OVERNIGHT] {sym} 失敗（台股指數）: {e}")

    return result


def compute_bias(data: dict) -> dict:
    """根據各指標漲跌幅，計算明日台股各板塊偏向。"""
    if not data:
        return {"overall": "無資料", "score": 50}

    # 只用美股（weight > 0）計算偏向
    us_data = {k: v for k, v in data.items() if v.get("weight", 0) > 0}
    sox_ret    = us_data.get("SOXX", {}).get("ret", 0)
    nvda_ret   = us_data.get("NVDA", {}).get("ret", 0)
    mu_ret     = us_data.get("MU",   {}).get("ret", 0)
    tsm_ret    = us_data.get("TSM",  {}).get("ret", 0)
    qqq_ret    = us_data.get("QQQ",  {}).get("ret", 0)
    sp500_ret  = us_data.get("^GSPC",{}).get("ret", 0)

    # 加權分數
    score = 50
    score += sox_ret * 3.0
    score += nvda_ret * 2.0
    score += mu_ret * 1.5     # 美光加入計算
    score += tsm_ret * 2.5
    score += qqq_ret * 1.5
    score += sp500_ret * 1.0
    score = round(max(0, min(100, score)), 1)

    # 台股指數資訊
    taiex = data.get("^TWII", {})
    tw_fut = data.get("^TWF", {})

    # 主題分數
    semi_score = min(100, max(0, 50 + (sox_ret + mu_ret/2 + tsm_ret) * 4))
    ai_score   = min(100, max(0, 50 + nvda_ret * 8))

    overall = "強勢偏多" if score >= 70 else "偏多" if score >= 58 else \
              "偏空" if score <= 42 else "偏多" if score <= 30 else "中性"

    # summary 加入美光
    mu_str = f" MU{mu_ret:+.1f}%" if mu_ret != 0 else ""
    summary_line = (f"美股夜盤 {overall} SOXX{sox_ret:+.1f}%"
                    f" NVDA{nvda_ret:+.1f}%{mu_str} TSM{tsm_ret:+.1f}%")

    # 加入台股指數點數（如果有）
    if taiex.get("close"):
        taiex_pt = round(taiex["close"])
        taiex_chg = taiex.get("point_change", 0)
        tw_fut_pt = round(tw_fut.get("close", 0)) if tw_fut.get("close") else None

        taiex_str = f"加權{taiex_pt:,}({taiex_chg:+.0f})"
        if tw_fut_pt:
            fut_chg = tw_fut.get("point_change", 0)
            taiex_str += f" 台指期{tw_fut_pt:,}({fut_chg:+.0f})"
        summary_line += f" | {taiex_str}"

    return {
        "overall":      overall,
        "score":        score,
        "semi_score":   round(semi_score, 1),
        "ai_score":     round(ai_score, 1),
        "summary":      summary_line,
        "taiex_close":  taiex.get("close"),
        "taiex_change": taiex.get("point_change"),
        "tw_futures_close":  tw_fut.get("close"),
        "tw_futures_change": tw_fut.get("point_change"),
        "mu_ret":       mu_ret,
    }


def save_to_db(bias: dict, data: dict):
    from backend.models.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        sox_ret  = data.get("SOXX",  {}).get("ret", 0)
        qqq_ret  = data.get("QQQ",   {}).get("ret", 0)
        sp500_ret= data.get("^GSPC", {}).get("ret", 0)
        tsm_ret  = data.get("TSM",   {}).get("ret", 0)
        mu_ret   = data.get("MU",    {}).get("ret", 0)
        score    = bias.get("score", 50)
        next_bias= bias.get("overall", "中性")
        summary  = bias.get("summary", "")

        db.execute(text("""
            INSERT INTO market_context_daily
                (context_date, overnight_score, next_day_bias,
                 sox_ret, qqq_ret, sp500_ret, tw_futures_ret, summary)
            VALUES (:d, :os, :nb, :sr, :qr, :sp, :tf, :sum)
            ON CONFLICT(context_date) DO UPDATE SET
                overnight_score=excluded.overnight_score,
                next_day_bias=excluded.next_day_bias,
                sox_ret=excluded.sox_ret,
                qqq_ret=excluded.qqq_ret,
                sp500_ret=excluded.sp500_ret,
                summary=excluded.summary
        """), {"d": today, "os": score, "nb": next_bias,
               "sr": sox_ret, "qr": qqq_ret, "sp": sp500_ret,
               "tf": tsm_ret, "sum": summary})
        db.commit()
        logger.info(f"[OVERNIGHT] 寫入 DB overnight_score={score} {next_bias}")
    except Exception as e:
        db.rollback()
        logger.error(f"[OVERNIGHT] DB 寫入失敗: {e}")
    finally:
        db.close()


def get_overnight_summary(force_refresh: bool = False) -> dict:
    """取得夜盤摘要（含快取）"""
    if not force_refresh and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
            cache_time = datetime.fromisoformat(cached.get("fetched_at", "2000-01-01"))
            if datetime.now() - cache_time < timedelta(hours=6):
                return cached
        except Exception:
            pass

    data = fetch_overnight()
    bias = compute_bias(data)
    save_to_db(bias, data)

    result = {
        "symbols": data,
        "bias": bias,
        "fetched_at": datetime.now().isoformat(),
        "taiex_close": bias.get("taiex_close"),
        "tw_futures_close": bias.get("tw_futures_close"),
        "mu_ret": bias.get("mu_ret", 0),
    }
    try:
        CACHE_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return result
