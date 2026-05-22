"""
backend/signals/scorer.py
計算所有分數並寫入 daily_scores
每日收盤後 EOD 完成後執行
"""
from datetime import date, timedelta
import pandas as pd
import numpy as np
from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, DailyScore, OHLCVDaily, ChipDaily
from config.settings import settings


# ════════════════════════════════════════════════
# 輔助函式
# ════════════════════════════════════════════════

def _percentile_score(series: pd.Series, lookback: int = 252) -> pd.Series:
    """將數值轉換為歷史百分位分數（0~100）"""
    return series.rolling(lookback, min_periods=20).rank(pct=True) * 100


def _minmax(val, lo, hi) -> float:
    """線性 minmax 正規化 → 0~100"""
    if hi == lo:
        return 50.0
    return float(np.clip((val - lo) / (hi - lo) * 100, 0, 100))


def _safe_float(x, default=None):
    """把 DB 讀到的數值安全轉成 float；空值 / 無限值回 default。"""
    if x is None:
        return default
    try:
        if isinstance(x, str):
            x = x.strip().replace(',', '').replace('%', '')
            if x in ('', '-', 'None', 'nan', 'NaN'):
                return default
        v = float(x)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _weighted_average(items, default=50.0) -> float:
    """items: [(score, weight), ...]，忽略 None。"""
    total, weight = 0.0, 0.0
    for score, w in items:
        score = _safe_float(score)
        w = _safe_float(w, 0.0)
        if score is None or w <= 0:
            continue
        total += score * w
        weight += w
    return total / weight if weight > 0 else default


def _event_date_to_date(x):
    """SQLAlchemy / SQLite 可能回 date 或 'YYYY-MM-DD' 字串。"""
    if isinstance(x, date):
        return x
    if x is None:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


# ════════════════════════════════════════════════
# 各分數計算模組
# ════════════════════════════════════════════════

class FundamentalScorer:
    """基本面 + 估值分數"""

    def score(self, code: str, score_date: date, db: Session) -> dict:
        # 1. 財報資料：之後 Step 3B 會補 EPS / ROE / 毛利率
        # 目前先保留 fundamental table 的查詢。
        sql = text("""
            SELECT eps, roe, gross_margin, op_margin, debt_ratio
            FROM fundamental
            WHERE code=:code
            ORDER BY year DESC, quarter DESC
            LIMIT 1
        """)
        row = db.execute(sql, {"code": code}).fetchone()

        # 2. 月營收資料：只吃 score_date 當下已公開的資料，避免偷看未來。
        # 若 monthly_revenue 還沒有 published_date 欄位，請先完成 Step 2。
        try:
            rev_sql = text("""
                SELECT yoy_pct
                FROM monthly_revenue
                WHERE code=:code
                  AND published_date <= :score_date
                  AND yoy_pct IS NOT NULL
                ORDER BY year DESC, month DESC
                LIMIT 3
            """)
            rev_rows = db.execute(
                rev_sql,
                {"code": code, "score_date": score_date}
            ).fetchall()
        except Exception:
            rev_sql = text("""
                SELECT yoy_pct
                FROM monthly_revenue
                WHERE code=:code
                  AND yoy_pct IS NOT NULL
                ORDER BY year DESC, month DESC
                LIMIT 3
            """)
            rev_rows = db.execute(rev_sql, {"code": code}).fetchall()

        yoy_values = []
        for r in rev_rows:
            try:
                yoy_values.append(float(r[0]))
            except (TypeError, ValueError):
                pass

        revenue_score = _minmax(float(np.mean(yoy_values)), -20, 30) if yoy_values else 50.0

        # 3. 估值資料：只吃 valuation_date <= score_date 的 PE/PB，避免偷看未來。
        val_sql = text("""
            SELECT pe, pb, dividend_yield
            FROM valuation_daily
            WHERE code=:code
              AND valuation_date <= :score_date
            ORDER BY valuation_date DESC
            LIMIT 1
        """)

        try:
            val_row = db.execute(
                val_sql,
                {"code": code, "score_date": score_date}
            ).fetchone()
        except Exception:
            val_row = None

        if val_row is None:
            valuation_score = 50.0
        else:
            pe, pb, dividend_yield = val_row

            pe = float(pe) if pe is not None else None
            pb = float(pb) if pb is not None else None
            dividend_yield = float(dividend_yield) if dividend_yield is not None else None

            # 用近一年 PE/PB 百分位：百分位越低 = 越便宜 = 分數越高
            pe_history = db.execute(text("""
                SELECT pe FROM valuation_daily
                WHERE code=:code AND valuation_date <= :score_date
                  AND pe IS NOT NULL AND pe > 0
                ORDER BY valuation_date DESC LIMIT 252
            """), {"code": code, "score_date": score_date}).fetchall()

            pb_history = db.execute(text("""
                SELECT pb FROM valuation_daily
                WHERE code=:code AND valuation_date <= :score_date
                  AND pb IS NOT NULL AND pb > 0
                ORDER BY valuation_date DESC LIMIT 252
            """), {"code": code, "score_date": score_date}).fetchall()

            def pct_score(history, val):
                if not history or val is None or val <= 0:
                    return 50.0
                vals = [float(r[0]) for r in history]
                pct = sum(1 for x in vals if x <= val) / len(vals)
                return round((1 - pct) * 100, 2)  # 百分位低 → 分數高

            parts = [
                pct_score(pe_history, pe),
                pct_score(pb_history, pb),
            ]

            # 殖利率：0~8% → 0~100 分，加分項
            if dividend_yield is not None and dividend_yield > 0:
                parts.append(_minmax(dividend_yield, 0, 8))
            else:
                parts.append(50.0)

            valuation_score = round(float(np.mean(parts)), 2)

        # 4. 基本面分數：如果季報還沒補，先由月營收 YoY 拉動。
        if row is None:
            # 無季報時，月營收 YoY 為主要依據（權重 80%），保留 20% neutral
            fundamental_score = round(revenue_score * 0.8 + 50.0 * 0.2, 2)

            return {
                "fundamental_score": fundamental_score,
                "valuation_score": valuation_score,
            }

        eps, roe, gm, opm, debt = (row[i] or 0 for i in range(5))

        f_components = [
            _minmax(roe, 0, 30),
            _minmax(gm, 10, 60),
            _minmax(opm, 0, 30),
            revenue_score,
            _minmax(eps, -5, 20),
        ]

        fundamental_score = round(float(np.mean(f_components)), 2)

        return {
            "fundamental_score": fundamental_score,
            "valuation_score": valuation_score,
        }


class ChipScorer:
    """籌碼分數：三大法人 + 融資融券"""

    def score(self, code: str, score_date: date, db: Session) -> dict:
        sql = text("""
            SELECT foreign_net, trust_net, dealer_net,
                   margin_balance, short_balance, margin_ratio
            FROM chip_daily
            WHERE code=:code AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 10
        """)
        rows = db.execute(sql, {"code": code, "d": score_date}).fetchall()
        if not rows:
            return {"chip_score": 50.0}

        df = pd.DataFrame(rows, columns=[
            "foreign_net","trust_net","dealer_net",
            "margin_balance","short_balance","margin_ratio"
        ])

        # 近 5 日外資累計
                # 三大法人欄位：空值視為 0
        for c in ["foreign_net", "trust_net", "dealer_net"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

        # 近 5 日外資 / 投信累計，自營商看最近 1 日
        foreign_5 = float(df["foreign_net"].head(5).sum())
        trust_5   = float(df["trust_net"].head(5).sum())
        dealer_1  = float(df["dealer_net"].iloc[0]) if len(df) else 0.0

        # 外資資料若全為 0（API 未提供），不納入計算
        scores = []
        if abs(foreign_5) > 100:
            scores.append(_minmax(foreign_5, -50000, 80000))
        # 投信（最重要）：連續買超是強訊號
        scores.append(_minmax(trust_5, -10000, 15000))
        # 自營商
        scores.append(_minmax(dealer_1, -5000, 5000))

        # 融資使用率目前還沒下載，所以 margin_ratio 可能是 NULL。
        # 沒資料時不要把它當 0，否則會被誤判成籌碼很乾淨。
        margin_ratio_series = pd.to_numeric(df["margin_ratio"], errors="coerce")
        latest_margin_ratio = margin_ratio_series.iloc[0] if len(margin_ratio_series) else np.nan

        if pd.isna(latest_margin_ratio):
            scores.append(50.0)
        else:
            margin_ratio = float(latest_margin_ratio)
            scores.append(_minmax(-margin_ratio, -80, -10))

        return {"chip_score": round(float(np.mean(scores)), 2)}


class MomentumScorer:
    """量價動能分數：成交量 + 內外盤比 + 技術指標"""

    def score(self, code: str, score_date: date, db: Session) -> dict:
        sql = text("""
            SELECT close, volume, value, change_pct
            FROM ohlcv_daily
            WHERE code=:code AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 60
        """)
        rows = db.execute(sql, {"code": code, "d": score_date}).fetchall()
        if len(rows) < 5:
            return {"momentum_score": 50.0}

        df = pd.DataFrame(rows, columns=["close","volume","value","change_pct"])
        df = df.iloc[::-1].reset_index(drop=True)  # 由舊到新

        close = df["close"]
        vol   = df["volume"]

        # 5/20 日均量比（量能放大）
        vol_ratio = vol.iloc[-5:].mean() / (vol.iloc[-20:].mean() + 1e-9)

        # 5/20 日均價比（短期趨勢）
        ma5  = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        price_trend = ma5 / (ma20 + 1e-9)

        # RSI(14)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = 100 - 100 / (1 + gain / (loss + 1e-9))
        rsi14 = float(rsi.iloc[-1]) if not rsi.empty else 50

        scores = [
            _minmax(vol_ratio,    0.3, 3.0),
            _minmax(price_trend,  0.9, 1.1),
            _minmax(rsi14,        20,  80),
        ]
        return {"momentum_score": round(float(np.mean(scores)), 2)}


class MacroScorer:
    """
    總經 / 大盤環境分數。

    原本是固定 55 分；現在改成只用 score_date 當下已存在的日 K 資料估算，
    因此回測時不會偷看未來。

    特色：
    - 全市場廣度：上漲家數比例、平均報酬、強漲強跌比例
    - 大盤 ETF 趨勢：0050 / 006208
    - 半導體權值股趨勢：2330 / 2454 / 2308 / 2383 / 3711

    注意：總經分數本來就屬於「全市場共用」因子，所以同一天所有股票會相同；
    但它不應該每天固定 55，現在會隨市場狀態變動。
    """

    MARKET_CODES = ["0050", "006208"]
    SEMI_CODES = ["2330", "2454", "2308", "2383", "3711"]

    def score(self, score_date: date, db: Session) -> dict:
        latest_trade_date = self._latest_trade_date(score_date, db)
        if latest_trade_date is None:
            return {"macro_score": 50.0}

        components = []
        market_score = self._score_market_breadth(latest_trade_date, db)
        if market_score is not None:
            components.append((market_score, 0.45))

        etf_score = self._score_code_group(self.MARKET_CODES, latest_trade_date, db)
        if etf_score is not None:
            components.append((etf_score, 0.30))

        semi_score = self._score_code_group(self.SEMI_CODES, latest_trade_date, db)
        if semi_score is not None:
            components.append((semi_score, 0.25))

        macro_score = _weighted_average(components, default=50.0)
        return {"macro_score": round(float(macro_score), 2)}

    def _latest_trade_date(self, score_date: date, db: Session):
        row = db.execute(
            text("""
                SELECT MAX(trade_date)
                FROM ohlcv_daily
                WHERE trade_date <= :d
            """),
            {"d": score_date},
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def _score_market_breadth(self, trade_date, db: Session):
        rows = db.execute(
            text("""
                SELECT close, change, change_pct
                FROM ohlcv_daily
                WHERE trade_date = :d
                  AND close IS NOT NULL
            """),
            {"d": trade_date},
        ).fetchall()
        if len(rows) < 50:
            return None

        rets = []
        for close, change, change_pct in rows:
            close = _safe_float(close)
            change = _safe_float(change)
            pct = _safe_float(change_pct)

            # 優先用 close / change 自己重算，避免來源 change_pct 欄位偶爾異常。
            if close is not None and change is not None:
                prev_close = close - change
                if prev_close and prev_close > 0:
                    pct = change / prev_close * 100

            if pct is not None:
                rets.append(float(np.clip(pct, -10, 10)))

        if len(rets) < 50:
            return None

        arr = np.array(rets, dtype=float)
        up_ratio = float(np.mean(arr > 0))
        avg_ret = float(np.mean(arr))
        strong_up = float(np.mean(arr >= 2.0))
        strong_down = float(np.mean(arr <= -2.0))
        risk_appetite = strong_up - strong_down

        breadth_score = _minmax(up_ratio, 0.25, 0.65)
        avg_ret_score = _minmax(avg_ret, -2.5, 2.5)
        risk_score = _minmax(risk_appetite, -0.25, 0.25)

        return _weighted_average([
            (breadth_score, 0.40),
            (avg_ret_score, 0.40),
            (risk_score, 0.20),
        ])

    def _score_code_group(self, codes: list[str], trade_date, db: Session):
        scores = []
        for code in codes:
            score = self._score_single_code_trend(code, trade_date, db)
            if score is not None:
                scores.append((score, 1.0))
        if not scores:
            return None
        return _weighted_average(scores)

    def _score_single_code_trend(self, code: str, trade_date, db: Session):
        rows = db.execute(
            text("""
                SELECT close
                FROM ohlcv_daily
                WHERE code = :code
                  AND trade_date <= :d
                  AND close IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 21
            """),
            {"code": code, "d": trade_date},
        ).fetchall()
        closes = [_safe_float(r[0]) for r in rows]
        closes = [x for x in closes if x is not None and x > 0]
        if len(closes) < 2:
            return None

        closes = list(reversed(closes))
        components = []

        ret_1d = closes[-1] / closes[-2] - 1
        components.append((_minmax(ret_1d * 100, -3, 3), 0.45))

        if len(closes) >= 6:
            ret_5d = closes[-1] / closes[-6] - 1
            components.append((_minmax(ret_5d * 100, -8, 8), 0.35))

        if len(closes) >= 21:
            ret_20d = closes[-1] / closes[0] - 1
            components.append((_minmax(ret_20d * 100, -15, 15), 0.20))

        return _weighted_average(components)


class NewsScorer:
    """
    新聞 / 法說會 / 重大事件分數。

    原本 news_events 沒資料時永遠 50 分；現在分兩層：
    1. 若 news_events 有資料：使用事件 sentiment / importance / event_type / title 關鍵字。
    2. 若 news_events 沒資料：用已公開的結構化事件做代理分數，包含：
       - 月營收 YoY / MoM
       - 當日或近期異常漲跌 + 放量
       - 近 5 日法人籌碼變化

    這些 fallback 都只查 <= score_date 的資料，避免回測偷看未來。
    """

    KEYWORDS_POSITIVE = [
        "創高", "獲利", "成長", "法說", "上調", "買進", "突破", "訂單", "擴產",
        "營收增", "年增", "轉盈", "優於預期", "調升", "受惠", "AI", "伺服器",
    ]
    KEYWORDS_NEGATIVE = [
        "虧損", "下修", "停工", "裁員", "罰款", "警示", "賣出", "跌停", "衰退",
        "營收減", "年減", "低於預期", "調降", "訴訟", "處分", "庫存", "砍單",
    ]

    def score(self, code: str, score_date: date, db: Session) -> dict:
        rows = self._fetch_news_events(code, score_date, db)
        if rows:
            news_score = self._score_news_events(rows, score_date)
        else:
            news_score = self._score_structured_event_proxy(code, score_date, db)
        return {"news_score": round(float(news_score), 2)}

    def _fetch_news_events(self, code: str, score_date: date, db: Session):
        start = score_date - timedelta(days=7)
        try:
            rows = db.execute(
                text("""
                    SELECT sentiment, importance, event_type, title, event_date
                    FROM news_events
                    WHERE (code = :code OR code IS NULL)
                      AND event_date BETWEEN :start AND :end
                      AND (title NOT LIKE '%籌碼事件%')
                    ORDER BY event_date DESC
                """),
                {"code": code, "start": start, "end": score_date},
            ).fetchall()
            return rows
        except Exception:
            return []

    def _score_news_events(self, rows, score_date: date) -> float:
        items = []
        for sentiment, importance, etype, title, event_date in rows:
            s = _safe_float(sentiment)
            if s is None:
                s = self._infer_sentiment_from_title(title)

            # sentiment -1~+1 先轉成 0~100。
            score = _minmax(float(np.clip(s, -1, 1)), -1.0, 1.0)
            w = _safe_float(importance, 0.5)

            if etype == "investor_conf":
                w *= 1.5
            elif etype == "macro":
                w *= 0.8

            ed = _event_date_to_date(event_date)
            if ed is not None:
                days_ago = max((score_date - ed).days, 0)
                w *= max(0.25, 1.0 - days_ago / 14.0)

            items.append((score, w))

        return _weighted_average(items, default=50.0)

    def _infer_sentiment_from_title(self, title) -> float:
        title = str(title or "")
        if not title:
            return 0.0

        score = 0.0
        for kw in self.KEYWORDS_POSITIVE:
            if kw in title:
                score += 0.25
        for kw in self.KEYWORDS_NEGATIVE:
            if kw in title:
                score -= 0.25
        return float(np.clip(score, -1.0, 1.0))

    def _score_structured_event_proxy(self, code: str, score_date: date, db: Session) -> float:
        components = []

        revenue_score = self._score_latest_revenue_event(code, score_date, db)
        if revenue_score is not None:
            components.append((revenue_score, 0.40))

        price_score = self._score_recent_price_event(code, score_date, db)
        if price_score is not None:
            components.append((price_score, 0.35))

        chip_score = self._score_recent_chip_event(code, score_date, db)
        if chip_score is not None:
            components.append((chip_score, 0.25))

        return _weighted_average(components, default=50.0)

    def _score_latest_revenue_event(self, code: str, score_date: date, db: Session):
        try:
            row = db.execute(
                text("""
                    SELECT yoy_pct, mom_pct, published_date
                    FROM monthly_revenue
                    WHERE code = :code
                      AND published_date <= :d
                    ORDER BY published_date DESC, year DESC, month DESC
                    LIMIT 1
                """),
                {"code": code, "d": score_date},
            ).fetchone()
        except Exception:
            return None

        if not row:
            return None

        yoy, mom, published_date = row
        yoy = _safe_float(yoy)
        mom = _safe_float(mom)

        items = []
        if yoy is not None:
            items.append((_minmax(yoy, -30, 50), 0.75))
        if mom is not None:
            items.append((_minmax(mom, -20, 20), 0.25))
        if not items:
            return None

        score = _weighted_average(items)

        # 太久以前公布的營收，仍可參考但事件性降低，往中性 50 收斂。
        pub = _event_date_to_date(published_date)
        if pub is not None:
            days_old = max((score_date - pub).days, 0)
            decay = max(0.35, 1.0 - days_old / 90.0)
            score = 50.0 + (score - 50.0) * decay

        return score

    def _score_recent_price_event(self, code: str, score_date: date, db: Session):
        rows = db.execute(
            text("""
                SELECT close, volume
                FROM ohlcv_daily
                WHERE code = :code
                  AND trade_date <= :d
                  AND close IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 21
            """),
            {"code": code, "d": score_date},
        ).fetchall()
        closes = [_safe_float(r[0]) for r in rows]
        vols = [_safe_float(r[1]) for r in rows]
        pairs = [(c, v) for c, v in zip(closes, vols) if c is not None and c > 0]
        if len(pairs) < 2:
            return None

        pairs = list(reversed(pairs))
        closes = [p[0] for p in pairs]
        vols = [p[1] for p in pairs]

        ret_1d = closes[-1] / closes[-2] - 1
        items = [(_minmax(ret_1d * 100, -7, 7), 0.45)]

        valid_vols = [v for v in vols[:-1] if v is not None and v > 0]
        if len(valid_vols) >= 5 and vols[-1] is not None and vols[-1] > 0:
            avg_vol = float(np.mean(valid_vols[-20:]))
            vol_ratio = vols[-1] / (avg_vol + 1e-9)
            shock = ret_1d * 100 * min(max(vol_ratio, 0.5), 2.0)
            items.append((_minmax(shock, -10, 10), 0.25))

        if len(closes) >= 6:
            ret_5d = closes[-1] / closes[-6] - 1
            items.append((_minmax(ret_5d * 100, -12, 12), 0.30))

        return _weighted_average(items)

    def _score_recent_chip_event(self, code: str, score_date: date, db: Session):
        rows = db.execute(
            text("""
                SELECT foreign_net, trust_net, dealer_net
                FROM chip_daily
                WHERE code = :code
                  AND trade_date <= :d
                ORDER BY trade_date DESC
                LIMIT 5
            """),
            {"code": code, "d": score_date},
        ).fetchall()
        if not rows:
            return None

        net_values = []
        for foreign_net, trust_net, dealer_net in rows:
            f = _safe_float(foreign_net, 0.0)
            t = _safe_float(trust_net, 0.0)
            d = _safe_float(dealer_net, 0.0)
            net_values.append(f + t + d)

        net_5d = float(np.sum(net_values))

        vol_row = db.execute(
            text("""
                SELECT AVG(volume)
                FROM (
                    SELECT volume
                    FROM ohlcv_daily
                    WHERE code = :code
                      AND trade_date <= :d
                      AND volume IS NOT NULL
                    ORDER BY trade_date DESC
                    LIMIT 20
                )
            """),
            {"code": code, "d": score_date},
        ).fetchone()
        avg_volume = _safe_float(vol_row[0] if vol_row else None)

        # chip_daily 常見單位是張；ohlcv_daily 常見單位可能是股或成交單位。
        # 這裡同時考慮 avg_volume/1000，避免法人買賣超被成交量單位放大或縮小。
        if avg_volume is not None and avg_volume > 0:
            avg_lots = max(avg_volume / 1000.0, 1.0)
            flow_ratio = net_5d / avg_lots
            return _minmax(flow_ratio, -0.30, 0.30)

        return _minmax(net_5d, -20000, 20000)


# ════════════════════════════════════════════════
# 主計算入口
# ════════════════════════════════════════════════

def compute_scores(codes: list[str], score_date: date = None):
    """計算所有股票分數並寫入 daily_scores"""
    if score_date is None:
        score_date = date.today()

    fs = FundamentalScorer()
    cs = ChipScorer()
    ms = MomentumScorer()
    mcs = MacroScorer()
    ns = NewsScorer()

    db = SessionLocal()
    macro = mcs.score(score_date, db)   # 全市場共用

    rows = []
    for code in codes:
        try:
            fd = fs.score(code, score_date, db)
            cd = cs.score(code, score_date, db)
            md = ms.score(code, score_date, db)
            nd = ns.score(code, score_date, db)

            w = settings.DEFAULT_WEIGHTS
            composite = (
                fd["fundamental_score"] * w["fundamental"] +
                fd["valuation_score"]   * w["valuation"] +
                cd["chip_score"]        * w["chip"] +
                md["momentum_score"]    * w["momentum"] +
                macro["macro_score"]    * w["macro"] +
                nd["news_score"]        * w["news"]
            )
            signal = "BUY" if composite >= 65 else ("SELL" if composite <= 35 else "HOLD")

            rows.append({
                "code":               code,
                "score_date":         score_date,
                "fundamental_score":  fd["fundamental_score"],
                "valuation_score":    fd["valuation_score"],
                "chip_score":         cd["chip_score"],
                "momentum_score":     md["momentum_score"],
                "macro_score":        macro["macro_score"],
                "news_score":         nd["news_score"],
                "composite_score":    round(composite, 2),
                "signal":             signal,
            })
        except Exception as e:
            logger.warning(f"compute_scores {code}: {e}")

    if rows:
        stmt = sqlite_insert(DailyScore).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "score_date"],
            set_={c: stmt.excluded[c] for c in
                  ["fundamental_score","valuation_score","chip_score",
                   "momentum_score","macro_score","news_score",
                   "composite_score","signal"]}
        )
        db.execute(stmt)
        db.commit()
        logger.success(f"[SCORE] {score_date} 計算完成，共 {len(rows)} 檔")

    db.close()
