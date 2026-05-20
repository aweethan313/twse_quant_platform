"""
backend/strategies/base_strategy.py

V4.5 策略層：
- 20 萬帳戶改用零股股數，不再以 1000 股整張下單。
- 交易日 T 僅使用 T 之前最近 score_date 的分數，避免偷看未來。
- 加入市場環境 market_context：夜盤 / 美股 proxy、台股廣度、成交量、主線題材。
- 加入量價結構：開收盤強弱、收盤位置、成交量比、內外盤比（若有分鐘資料）。
- S3 籌碼策略改為可交易：不再卡死 signal=BUY，也不要求過高 chip 門檻。
- 新增 S6 主線題材趨勢策略，專門測 AI / 半導體等主線輪動。
"""
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Optional

import pandas as pd
from loguru import logger
from sqlalchemy import text

from backend.models.database import SessionLocal, StrategyAccount, Position
from backend.engine.paper_account import PaperAccount
from config.settings import settings


AI_THEME_CODES = {
    "2330", "2454", "2308", "2383", "2449", "3037", "3189", "6205", "3711",
    "2303", "2344", "2327", "3661", "3443", "3017", "3324", "3653", "6274",
    "6669", "6488", "5274", "6415", "8210", "3013", "2368", "3533", "6531",
}
PCB_THEME_CODES = {"2383", "3037", "3189", "2368", "6274", "4958", "8046", "2313", "6191"}
POWER_COOLING_CODES = {"2308", "3017", "3324", "3653", "2421", "6230", "8996", "3533"}


THEME_CODE_MAP = {
    "AI/半導體": AI_THEME_CODES,
    "PCB/載板": PCB_THEME_CODES,
    "電源/散熱": POWER_COOLING_CODES,
}


def _to_date(x) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    return date.fromisoformat(str(x)[:10])


def _safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        if pd.isna(v):
            return default
        return v
    except Exception:
        return default


class BaseStrategy(ABC):
    """
    所有策略繼承此類別。

    run(trade_date) 的 trade_date 是實際模擬成交日。
    進出場判斷只使用 score_date < trade_date 的最新分數與歷史資料。
    """

    def __init__(self, account_id: int, params: dict = None):
        self.account_id = account_id
        self.params = params or {}
        self.broker = PaperAccount(account_id)

    @abstractmethod
    def should_enter(self, code: str, scores: dict, ohlcv: dict) -> tuple[bool, str]:
        ...

    @abstractmethod
    def should_exit(self, code: str, position: dict, scores: dict, ohlcv: dict) -> tuple[bool, str]:
        ...

    # ─────────────────────────────────────────────
    # 通用資料 / 風控工具
    # ─────────────────────────────────────────────

    def lot_size(self, cash: float, price: float) -> int:
        """
        回傳買入股數 shares。
        參數名沿用舊版 lot_size，但 V4.5 起不再買整張，適合 20 萬帳戶零股回測。
        """
        if cash <= 0 or price <= 0:
            return 0
        max_pct = float(self.params.get("max_pct", 0.18))
        max_invest = cash * max_pct
        min_order_amount = float(self.params.get("min_order_amount", 3000))
        if max_invest < min_order_amount:
            return 0
        shares = int(max_invest / price)
        max_shares = int(self.params.get("max_shares", 999999))
        min_shares = int(self.params.get("min_shares", 1))
        shares = max(0, min(shares, max_shares))
        return shares if shares >= min_shares else 0

    @staticmethod
    def _score_date_for_trade(db, trade_date: date) -> Optional[date]:
        row = db.execute(
            text("SELECT MAX(score_date) FROM daily_scores WHERE score_date < :d"),
            {"d": trade_date},
        ).fetchone()
        return _to_date(row[0]) if row and row[0] else None

    @staticmethod
    def _fetch_scores(db, code: str, score_date: date) -> dict:
        sql = text("""
            SELECT fundamental_score, valuation_score, chip_score,
                   momentum_score, macro_score, news_score, composite_score, signal
            FROM daily_scores
            WHERE code=:code AND score_date=:d
        """)
        row = db.execute(sql, {"code": code, "d": score_date}).fetchone()
        if row is None:
            return {}
        keys = ["fundamental", "valuation", "chip", "momentum", "macro", "news", "composite", "signal"]
        return dict(zip(keys, row))

    def _fetch_top_candidates(self, db, score_date: date, top_n: int = None):
        top_n = int(top_n or self.params.get("candidate_top_n", 160))
        min_comp = float(self.params.get("candidate_min_composite", 38))
        sql = text("""
            SELECT code, fundamental_score, valuation_score, chip_score,
                   momentum_score, macro_score, news_score, composite_score, signal
            FROM daily_scores
            WHERE score_date=:d
              AND composite_score >= :min_comp
            ORDER BY composite_score DESC
            LIMIT :n
        """)
        rows = db.execute(sql, {"d": score_date, "n": top_n, "min_comp": min_comp}).fetchall()
        keys = ["fundamental", "valuation", "chip", "momentum", "macro", "news", "composite", "signal"]
        return [(str(r[0]), dict(zip(keys, r[1:]))) for r in rows]

    @staticmethod
    def _fetch_history(db, code: str, score_date: date, limit: int = 80) -> pd.DataFrame:
        rows = db.execute(
            text("""
                SELECT trade_date, open, high, low, close, volume, value, change_pct
                FROM ohlcv_daily
                WHERE code=:code AND trade_date <= :d
                ORDER BY trade_date DESC
                LIMIT :n
            """),
            {"code": code, "d": score_date, "n": limit},
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "value", "change_pct"])
        df = df.iloc[::-1].reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume", "value", "change_pct"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close"])

    @staticmethod
    def _features_from_history(df: pd.DataFrame) -> dict:
        if df is None or df.empty:
            return {}
        close = df["close"]
        vol = df["volume"].fillna(0)
        last = df.iloc[-1]
        prev_close = close.iloc[-2] if len(close) >= 2 else close.iloc[-1]
        high = _safe_float(last.get("high"), 0)
        low = _safe_float(last.get("low"), 0)
        op = _safe_float(last.get("open"), 0)
        cl = _safe_float(last.get("close"), 0)
        feat = {
            "score_close": float(close.iloc[-1]),
            "score_change_pct": _safe_float(last.get("change_pct"), 0.0),
            "ret1": 0.0,
            "ret3": 0.0,
            "ret5": 0.0,
            "ret20": 0.0,
            "ma5": float(close.rolling(5, min_periods=1).mean().iloc[-1]),
            "ma10": float(close.rolling(10, min_periods=1).mean().iloc[-1]),
            "ma20": float(close.rolling(20, min_periods=1).mean().iloc[-1]),
            "ma60": float(close.rolling(60, min_periods=1).mean().iloc[-1]),
            "vol_ratio": 1.0,
            "open_to_close_pct": (cl / op - 1) * 100 if op > 0 else 0.0,
            "gap_pct": (op / prev_close - 1) * 100 if prev_close > 0 else 0.0,
            "close_position": (cl - low) / (high - low) if high > low else 0.5,
        }
        for n in [1, 3, 5, 20]:
            if len(close) > n and close.iloc[-1 - n] > 0:
                feat[f"ret{n}"] = float(close.iloc[-1] / close.iloc[-1 - n] - 1)
        if len(vol) >= 20 and vol.iloc[-20:].mean() > 0:
            feat["vol_ratio"] = float(vol.iloc[-5:].mean() / (vol.iloc[-20:].mean() + 1e-9))
        elif len(vol) >= 5 and vol.iloc[:-5].mean() > 0:
            feat["vol_ratio"] = float(vol.iloc[-5:].mean() / (vol.iloc[:-5].mean() + 1e-9))
        return feat

    @staticmethod
    def _fetch_market_context(db, score_date: date) -> dict:
        row = db.execute(
            text("""
                SELECT context_date, market_bias_score, next_day_bias, trend_regime,
                       breadth_score, volume_score, overnight_score,
                       nasdaq_ret, sox_ret, qqq_ret, sp500_ret, tw_futures_ret,
                       top_theme, top_theme_score, ai_theme_score, summary
                FROM market_context_daily
                WHERE context_date <= :d
                ORDER BY context_date DESC LIMIT 1
            """),
            {"d": score_date},
        ).fetchone()
        if not row:
            return {
                "market_bias_score": 50.0,
                "next_day_bias": "中性",
                "trend_regime": "未知",
                "top_theme": None,
                "top_theme_score": 50.0,
                "ai_theme_score": 50.0,
                "summary": "尚未建立 market_context_daily，請先跑 scripts.update_market_context",
            }
        keys = [
            "context_date", "market_bias_score", "next_day_bias", "trend_regime",
            "breadth_score", "volume_score", "overnight_score",
            "nasdaq_ret", "sox_ret", "qqq_ret", "sp500_ret", "tw_futures_ret",
            "top_theme", "top_theme_score", "ai_theme_score", "summary",
        ]
        d = dict(zip(keys, row))
        for k in ["market_bias_score", "breadth_score", "volume_score", "overnight_score", "top_theme_score", "ai_theme_score"]:
            d[k] = _safe_float(d.get(k), 50)
        return d

    @staticmethod
    def _fetch_stock_structure(db, code: str, score_date: date) -> dict:
        row = db.execute(
            text("""
                SELECT open_to_close_pct, close_position, volume_ratio,
                       buy_volume, sell_volume, buy_sell_ratio
                FROM stock_structure_daily
                WHERE code=:code AND feature_date <= :d
                ORDER BY feature_date DESC LIMIT 1
            """),
            {"code": code, "d": score_date},
        ).fetchone()
        if not row:
            return {}
        return {
            "structure_open_to_close_pct": _safe_float(row[0], 0),
            "structure_close_position": _safe_float(row[1], 0.5),
            "structure_volume_ratio": _safe_float(row[2], 1),
            "buy_volume": _safe_float(row[3], 0),
            "sell_volume": _safe_float(row[4], 0),
            "buy_sell_ratio": _safe_float(row[5], 1),
        }

    def _theme_boost(self, code: str, market_context: dict) -> float:
        top_theme = market_context.get("top_theme")
        top_score = _safe_float(market_context.get("top_theme_score"), 50)
        boost = 0.0
        if top_score >= self.params.get("theme_min_score", 58):
            if top_theme in THEME_CODE_MAP and code in THEME_CODE_MAP[top_theme]:
                boost = min(8.0, (top_score - 55) / 4)
        # AI/半導體主線加分
        ai_score = _safe_float(market_context.get("ai_theme_score"), 50)
        if code in AI_THEME_CODES and ai_score >= 60:
            boost = max(boost, 3.0)
        # V2 P4: 夜盤半導體強勢時額外加分
        overnight = _safe_float(market_context.get("overnight_score"), 50)
        sox_ret = _safe_float(market_context.get("sox_ret"), 0)
        if code in AI_THEME_CODES and sox_ret > 1.5:   # SOXX 漲超 1.5%
            boost += 2.0
        if code in AI_THEME_CODES and overnight > 60:  # 綜合偏多
            boost += 1.0
        return min(boost, 10.0)  # 最多加 10 分

    def _build_ohlcv(self, db, code: str, trade_date: date, score_date: date, execution_price: float) -> dict:
        hist = self._fetch_history(db, code, score_date, limit=int(self.params.get("history_limit", 80)))
        feat = self._features_from_history(hist)
        market_context = self._fetch_market_context(db, score_date)
        feat.update(self._fetch_stock_structure(db, code, score_date))
        # V2: 從 daily_scores 補充技術欄位備援
        _ds = db.execute(text(
            "SELECT vol_ratio,buy_sell_ratio,open_to_close_pct,close_position,ret1,ret5,ret20 "
            "FROM daily_scores WHERE code=:c AND score_date=:d"
        ), {"c": code, "d": score_date}).fetchone()
        if _ds:
            feat.setdefault("vol_ratio",             _safe_float(_ds[0], 1.0))
            feat.setdefault("buy_sell_ratio",         _safe_float(_ds[1], 1.0))
            feat.setdefault("open_to_close_pct",      _safe_float(_ds[2], 0.0))
            feat.setdefault("close_position",         _safe_float(_ds[3], 0.5))
            feat.setdefault("structure_volume_ratio", _safe_float(_ds[0], 1.0))
            if not feat.get("ret1"):  feat["ret1"]  = _safe_float(_ds[4], 0.0)
            if not feat.get("ret5"):  feat["ret5"]  = _safe_float(_ds[5], 0.0)
            if not feat.get("ret20"): feat["ret20"] = _safe_float(_ds[6], 0.0)
        feat.update({
            "close": execution_price,
            "trade_date": trade_date,
            "score_date": score_date,
            "history": hist,
            "market_context": market_context,
            "theme_boost": self._theme_boost(code, market_context),
        })
        return feat

    def _common_market_ok(self, scores: dict, ohlcv: dict | None = None) -> tuple[bool, str]:
        macro = _safe_float(scores.get("macro"), 50)
        news = _safe_float(scores.get("news"), 50)
        if macro < self.params.get("min_macro", 38):
            return False, f"大盤分數過低 macro={macro:.1f}"
        if news < self.params.get("min_news", 38):
            return False, f"事件分數過低 news={news:.1f}"
        if ohlcv is not None:
            ctx = ohlcv.get("market_context") or {}
            market_bias = _safe_float(ctx.get("market_bias_score"), 50)
            if market_bias < self.params.get("min_market_bias", 36):
                return False, f"隔日盤勢分過低 market_bias={market_bias:.1f}"
        return True, ""

    def _not_overheated(self, ohlcv: dict) -> tuple[bool, str]:
        ret1 = _safe_float(ohlcv.get("ret1"), 0)
        ret5 = _safe_float(ohlcv.get("ret5"), 0)
        volr = max(_safe_float(ohlcv.get("vol_ratio"), 1), _safe_float(ohlcv.get("structure_volume_ratio"), 1))
        max_ret1 = self.params.get("max_score_day_return", 0.085)
        max_ret5 = self.params.get("max_5d_return", 0.22)
        max_vol_ratio = self.params.get("max_vol_ratio", 4.8)
        if ret1 > max_ret1:
            return False, f"前一日漲幅過熱 ret1={ret1:.2%}"
        if ret5 > max_ret5:
            return False, f"近5日漲幅過熱 ret5={ret5:.2%}"
        if volr > max_vol_ratio and ret1 > 0.05:
            return False, f"爆量追高風險 vol_ratio={volr:.2f}"
        return True, ""

    def _structure_ok(self, ohlcv: dict) -> tuple[bool, str]:
        close_pos = _safe_float(ohlcv.get("structure_close_position", ohlcv.get("close_position")), 0.5)
        otc = _safe_float(ohlcv.get("structure_open_to_close_pct", ohlcv.get("open_to_close_pct")), 0)
        bsr = _safe_float(ohlcv.get("buy_sell_ratio"), 1)
        if close_pos < self.params.get("min_close_position", 0.28):
            return False, f"收盤位置偏弱 close_position={close_pos:.2f}"
        if otc < self.params.get("min_open_to_close_pct", -4.0):
            return False, f"開高走低/開收盤偏弱 open_to_close={otc:.2f}%"
        if bsr < self.params.get("min_buy_sell_ratio", 0.75):
            return False, f"內外盤偏賣方 buy_sell_ratio={bsr:.2f}"
        return True, ""

    def _trend_not_broken(self, ohlcv: dict, tolerance: float = 0.97) -> tuple[bool, str]:
        close = _safe_float(ohlcv.get("score_close"), 0)
        ma20 = _safe_float(ohlcv.get("ma20"), 0)
        if ma20 > 0 and close < ma20 * tolerance:
            return False, f"跌破MA20過多 close={close:.2f} ma20={ma20:.2f}"
        return True, ""

    def run(self, trade_date: date, price_map: dict[str, float]):
        db = SessionLocal()
        try:
            acc = db.query(StrategyAccount).filter_by(id=self.account_id).first()
            if not acc or not acc.is_active:
                return

            score_date = self._score_date_for_trade(db, trade_date)
            if score_date is None:
                self.broker.snapshot_equity(price_map, trade_date)
                logger.warning(f"[STRATEGY][{self.account_id}] {trade_date} 找不到前一日分數，僅做快照")
                return

            positions = {
                p.code: {"lots": int(p.lots or 0), "avg_cost": p.avg_cost}
                for p in db.query(Position).filter_by(account_id=self.account_id).all()
            }

            # 出場：只對既有部位判斷，不會當日買進又當日賣出。
            for code, pos in list(positions.items()):
                price = price_map.get(code)
                if price is None:
                    continue
                scores = self._fetch_scores(db, code, score_date)
                if not scores:
                    continue
                ohlcv = self._build_ohlcv(db, code, trade_date, score_date, price)
                exit_, reason = self.should_exit(code, pos, scores, ohlcv)
                if exit_:
                    result = self.broker.sell(code, pos["lots"], price, reason, trade_date)
                    if result.ok:
                        positions.pop(code, None)

            # 進場。
            db.refresh(acc)
            max_pos = int(self.params.get("max_positions", 5))
            candidates = self._fetch_top_candidates(db, score_date)
            for code, scores in candidates:
                if len(positions) >= max_pos:
                    break
                if code in positions:
                    continue
                price = price_map.get(code)
                if not price:
                    continue
                ohlcv = self._build_ohlcv(db, code, trade_date, score_date, price)
                enter, reason = self.should_enter(code, scores, ohlcv)
                if not enter:
                    continue
                shares = self.lot_size(acc.cash, price)
                # V2 P4: 夜盤偏空時縮減倉位
                _overnight = _safe_float(
                    (ohlcv.get("market_context") or {}).get("overnight_score"), 50
                )
                if _overnight < 42:       # 強勢偏空：縮到 60%
                    shares = int(shares * 0.6)
                elif _overnight < 47:     # 偏空：縮到 75%
                    shares = int(shares * 0.75)
                if shares < 1:
                    continue
                result = self.broker.buy(code, shares, price, reason, trade_date)
                if result.ok:
                    est_spend = shares * price * (1 + settings.TRADE_FEE_RATE)
                    acc.cash -= est_spend
                    positions[code] = {"lots": shares, "avg_cost": price}

            self.broker.snapshot_equity(price_map, trade_date)
        finally:
            db.close()


# ════════════════════════════════════════════════
# 策略一：改良動能突破
# ════════════════════════════════════════════════

class MomentumBreakout(BaseStrategy):
    def should_enter(self, code, scores, ohlcv):
        ok, reason = self._common_market_ok(scores, ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._not_overheated(ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._trend_not_broken(ohlcv, tolerance=self.params.get("ma20_tolerance", 0.98))
        if not ok:
            return False, reason
        ok, reason = self._structure_ok(ohlcv)
        if not ok:
            return False, reason

        boost = _safe_float(ohlcv.get("theme_boost"), 0)
        comp = _safe_float(scores.get("composite"), 0)
        mom = _safe_float(scores.get("momentum"), 0)
        chip = _safe_float(scores.get("chip"), 0)
        volr = max(_safe_float(ohlcv.get("vol_ratio"), 1), _safe_float(ohlcv.get("structure_volume_ratio"), 1))
        ret5 = _safe_float(ohlcv.get("ret5"), 0)

        if (comp + boost >= self.params.get("entry_composite", 54)
            and mom + boost >= self.params.get("entry_momentum", 60)
            and chip >= self.params.get("entry_chip", 40)
            and volr >= self.params.get("min_vol_ratio", 0.80)
            and ret5 >= self.params.get("min_5d_return", -0.04)):
            return True, f"改良動能 comp={comp:.1f} mom={mom:.1f} chip={chip:.1f} theme+{boost:.1f} ret5={ret5:.2%}"
        return False, ""

    def should_exit(self, code, position, scores, ohlcv):
        cost = position["avg_cost"]
        price = ohlcv["close"]
        ret = (price - cost) / cost if cost else 0
        mom = _safe_float(scores.get("momentum"), 50)
        comp = _safe_float(scores.get("composite"), 50)
        macro = _safe_float(scores.get("macro"), 50)
        market_bias = _safe_float((ohlcv.get("market_context") or {}).get("market_bias_score"), 50)
        score_close = _safe_float(ohlcv.get("score_close"), price)
        ma20 = _safe_float(ohlcv.get("ma20"), 0)

        if ret <= self.params.get("stop_loss", -0.05):
            return True, f"風控停損 {ret:.2%}"
        if ma20 > 0 and score_close < ma20 * self.params.get("exit_ma20_ratio", 0.965):
            return True, f"跌破趨勢 MA20 score_close={score_close:.2f} ma20={ma20:.2f}"
        if macro < self.params.get("exit_macro", 36) or market_bias < self.params.get("exit_market_bias", 34):
            return True, f"大盤風險升高 macro={macro:.1f} market_bias={market_bias:.1f}"
        if comp < self.params.get("weak_composite", 40) or mom < self.params.get("weak_momentum", 36):
            return True, f"動能轉弱 comp={comp:.1f} mom={mom:.1f}"
        if ret >= self.params.get("take_profit", 0.14):
            return True, f"分批停利條件 {ret:.2%}"
        return False, ""


class ValueReversion(BaseStrategy):
    def should_enter(self, code, scores, ohlcv):
        ok, reason = self._common_market_ok(scores, ohlcv)
        if not ok:
            return False, reason

        f = _safe_float(scores.get("fundamental"), 0)
        v = _safe_float(scores.get("valuation"), 0)
        c = _safe_float(scores.get("chip"), 0)
        m = _safe_float(scores.get("momentum"), 0)
        ret5 = _safe_float(ohlcv.get("ret5"), 0)
        ret20 = _safe_float(ohlcv.get("ret20"), 0)
        score_close = _safe_float(ohlcv.get("score_close"), 0)
        ma20 = _safe_float(ohlcv.get("ma20"), 0)

        if ma20 > 0 and score_close < ma20 * self.params.get("ma20_tolerance", 0.93):
            return False, "價值股仍在破線下跌"
        if ret5 > self.params.get("max_5d_return", 0.13):
            return False, "價值策略不追近5日急漲"
        if ret20 < self.params.get("min_20d_return", -0.18):
            return False, "中期下跌趨勢過強"

        if (f >= self.params.get("entry_fundamental", 50)
            and v >= self.params.get("entry_valuation", 52)
            and c >= self.params.get("entry_chip", 38)
            and m >= self.params.get("entry_momentum", 26)):
            return True, f"品質價值 f={f:.1f} v={v:.1f} chip={c:.1f} mom={m:.1f}"
        return False, ""

    def should_exit(self, code, position, scores, ohlcv):
        cost = position["avg_cost"]
        price = ohlcv["close"]
        ret = (price - cost) / cost if cost else 0
        v = _safe_float(scores.get("valuation"), 50)
        c = _safe_float(scores.get("chip"), 50)
        score_close = _safe_float(ohlcv.get("score_close"), price)
        ma20 = _safe_float(ohlcv.get("ma20"), 0)

        if ret <= self.params.get("stop_loss", -0.07):
            return True, f"價值策略停損 {ret:.2%}"
        if ma20 > 0 and score_close < ma20 * self.params.get("exit_ma20_ratio", 0.92):
            return True, f"價值股趨勢失守 close={score_close:.2f} ma20={ma20:.2f}"
        if c < self.params.get("exit_chip", 30):
            return True, f"籌碼惡化 chip={c:.1f}"
        if ret >= self.params.get("take_profit", 0.13) and v < self.params.get("valuation_after_profit", 43):
            return True, f"漲後估值不便宜 ret={ret:.2%} v={v:.1f}"
        if ret >= self.params.get("hard_take_profit", 0.20):
            return True, f"價值策略停利 {ret:.2%}"
        return False, ""


class ChipFollow(BaseStrategy):
    """S3：籌碼趨勢跟隨。V4.5 降低卡死門檻，加入量價與內外盤確認。"""

    def should_enter(self, code, scores, ohlcv):
        ok, reason = self._common_market_ok(scores, ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._not_overheated(ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._structure_ok(ohlcv)
        if not ok:
            return False, reason

        boost = _safe_float(ohlcv.get("theme_boost"), 0)
        chip = _safe_float(scores.get("chip"), 0)
        news = _safe_float(scores.get("news"), 50)
        mom = _safe_float(scores.get("momentum"), 0)
        comp = _safe_float(scores.get("composite"), 0)
        ret5 = _safe_float(ohlcv.get("ret5"), 0)
        volr = max(_safe_float(ohlcv.get("vol_ratio"), 1), _safe_float(ohlcv.get("structure_volume_ratio"), 1))
        bsr = _safe_float(ohlcv.get("buy_sell_ratio"), 1)

        chip_ok = chip + boost >= self.params.get("entry_chip", 49)
        flow_ok = volr >= self.params.get("min_vol_ratio", 0.75) and bsr >= self.params.get("min_buy_sell_ratio", 0.75)
        score_ok = (news + boost >= self.params.get("entry_news", 42)
                    and mom + boost >= self.params.get("entry_momentum", 38)
                    and comp + boost >= self.params.get("entry_composite", 46)
                    and ret5 >= self.params.get("min_5d_return", -0.07))
        if chip_ok and flow_ok and score_ok:
            return True, f"籌碼趨勢 chip={chip:.1f} news={news:.1f} mom={mom:.1f} comp={comp:.1f} vol={volr:.2f} bsr={bsr:.2f} theme+{boost:.1f}"
        return False, ""

    def should_exit(self, code, position, scores, ohlcv):
        cost = position["avg_cost"]
        price = ohlcv["close"]
        ret = (price - cost) / cost if cost else 0
        chip = _safe_float(scores.get("chip"), 50)
        mom = _safe_float(scores.get("momentum"), 50)
        bsr = _safe_float(ohlcv.get("buy_sell_ratio"), 1)
        if ret <= self.params.get("stop_loss", -0.05):
            return True, f"籌碼策略停損 {ret:.2%}"
        if chip < self.params.get("exit_chip", 35):
            return True, f"籌碼轉弱 chip={chip:.1f}"
        if bsr < self.params.get("exit_buy_sell_ratio", 0.60):
            return True, f"內外盤轉弱 buy_sell_ratio={bsr:.2f}"
        if ret > 0.05 and mom < self.params.get("exit_momentum_after_profit", 40):
            return True, f"獲利後動能轉弱 mom={mom:.1f} ret={ret:.2%}"
        if ret >= self.params.get("take_profit", 0.16):
            return True, f"籌碼策略停利 {ret:.2%}"
        return False, ""


class BalancedScoreStrategy(BaseStrategy):
    def should_enter(self, code, scores, ohlcv):
        ok, reason = self._common_market_ok(scores, ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._not_overheated(ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._trend_not_broken(ohlcv, tolerance=self.params.get("ma20_tolerance", 0.96))
        if not ok:
            return False, reason

        boost = _safe_float(ohlcv.get("theme_boost"), 0)
        comp = _safe_float(scores.get("composite"), 0)
        f = _safe_float(scores.get("fundamental"), 0)
        v = _safe_float(scores.get("valuation"), 0)
        c = _safe_float(scores.get("chip"), 0)
        m = _safe_float(scores.get("momentum"), 0)
        n = _safe_float(scores.get("news"), 50)

        if (comp + boost >= self.params.get("entry_composite", 51)
            and f >= self.params.get("entry_fundamental", 42)
            and v >= self.params.get("entry_valuation", 42)
            and c >= self.params.get("entry_chip", 39)
            and m + boost >= self.params.get("entry_momentum", 42)
            and n + boost >= self.params.get("entry_news", 42)):
            return True, f"均衡分數 comp={comp:.1f} f={f:.1f} v={v:.1f} c={c:.1f} m={m:.1f} theme+{boost:.1f}"
        return False, ""

    def should_exit(self, code, position, scores, ohlcv):
        cost = position["avg_cost"]
        price = ohlcv["close"]
        ret = (price - cost) / cost if cost else 0
        comp = _safe_float(scores.get("composite"), 50)
        macro = _safe_float(scores.get("macro"), 50)
        if ret <= self.params.get("stop_loss", -0.055):
            return True, f"均衡策略停損 {ret:.2%}"
        if comp < self.params.get("exit_composite", 40):
            return True, f"綜合分轉弱 comp={comp:.1f}"
        if macro < self.params.get("exit_macro", 36):
            return True, f"大盤風險 macro={macro:.1f}"
        if ret >= self.params.get("take_profit", 0.14):
            return True, f"均衡策略停利 {ret:.2%}"
        return False, ""


class PullbackTrendStrategy(BaseStrategy):
    def should_enter(self, code, scores, ohlcv):
        ok, reason = self._common_market_ok(scores, ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._structure_ok(ohlcv)
        if not ok:
            return False, reason

        boost = _safe_float(ohlcv.get("theme_boost"), 0)
        comp = _safe_float(scores.get("composite"), 0)
        mom = _safe_float(scores.get("momentum"), 0)
        chip = _safe_float(scores.get("chip"), 0)
        score_close = _safe_float(ohlcv.get("score_close"), 0)
        ma10 = _safe_float(ohlcv.get("ma10"), 0)
        ma20 = _safe_float(ohlcv.get("ma20"), 0)
        ret1 = _safe_float(ohlcv.get("ret1"), 0)
        ret5 = _safe_float(ohlcv.get("ret5"), 0)
        ret20 = _safe_float(ohlcv.get("ret20"), 0)

        if ma20 <= 0 or ma10 <= 0:
            return False, "均線資料不足"
        if not (score_close >= ma20 * self.params.get("ma20_tolerance", 0.98)):
            return False, "中期趨勢不足"
        if ret20 < self.params.get("min_20d_return", -0.01):
            return False, "20日趨勢不夠強"
        if not (self.params.get("pullback_ret1_min", -0.06) <= ret1 <= self.params.get("pullback_ret1_max", 0.035)):
            return False, "不是短線回檔型態"
        if ret5 > self.params.get("max_5d_return", 0.14):
            return False, "近5日漲太多"

        if (comp + boost >= self.params.get("entry_composite", 48)
            and mom + boost >= self.params.get("entry_momentum", 39)
            and chip >= self.params.get("entry_chip", 36)):
            return True, f"趨勢回檔 comp={comp:.1f} mom={mom:.1f} chip={chip:.1f} ret1={ret1:.2%} ret20={ret20:.2%} theme+{boost:.1f}"
        return False, ""

    def should_exit(self, code, position, scores, ohlcv):
        cost = position["avg_cost"]
        price = ohlcv["close"]
        ret = (price - cost) / cost if cost else 0
        score_close = _safe_float(ohlcv.get("score_close"), price)
        ma20 = _safe_float(ohlcv.get("ma20"), 0)
        if ret <= self.params.get("stop_loss", -0.048):
            return True, f"回檔策略停損 {ret:.2%}"
        if ma20 > 0 and score_close < ma20 * self.params.get("exit_ma20_ratio", 0.96):
            return True, f"趨勢失守 close={score_close:.2f} ma20={ma20:.2f}"
        if ret >= self.params.get("take_profit", 0.11):
            return True, f"回檔策略停利 {ret:.2%}"
        return False, ""


class ThemeTrendStrategy(BaseStrategy):
    """S6：主線題材趨勢策略。當市場主線偏 AI/半導體、PCB、散熱時，只買主線中的強勢股。"""

    def should_enter(self, code, scores, ohlcv):
        ctx = ohlcv.get("market_context") or {}
        top_theme = ctx.get("top_theme")
        top_score = _safe_float(ctx.get("top_theme_score"), 50)
        market_bias = _safe_float(ctx.get("market_bias_score"), 50)
        allowed_codes = THEME_CODE_MAP.get(top_theme, set())
        if not allowed_codes or code not in allowed_codes:
            return False, "非目前主線題材"
        if top_score < self.params.get("entry_theme_score", 60):
            return False, f"主線分數不足 theme={top_theme} score={top_score:.1f}"
        if market_bias < self.params.get("min_market_bias", 38):
            return False, f"隔日盤勢分不足 market_bias={market_bias:.1f}"

        ok, reason = self._not_overheated(ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._structure_ok(ohlcv)
        if not ok:
            return False, reason
        ok, reason = self._trend_not_broken(ohlcv, tolerance=self.params.get("ma20_tolerance", 0.965))
        if not ok:
            return False, reason

        comp = _safe_float(scores.get("composite"), 0)
        mom = _safe_float(scores.get("momentum"), 0)
        chip = _safe_float(scores.get("chip"), 0)
        news = _safe_float(scores.get("news"), 50)
        volr = max(_safe_float(ohlcv.get("vol_ratio"), 1), _safe_float(ohlcv.get("structure_volume_ratio"), 1))
        if (comp >= self.params.get("entry_composite", 47)
            and mom >= self.params.get("entry_momentum", 40)
            and chip >= self.params.get("entry_chip", 34)
            and news >= self.params.get("entry_news", 40)
            and volr >= self.params.get("min_vol_ratio", 0.75)):
            return True, f"主線題材 {top_theme} theme={top_score:.1f} market={market_bias:.1f} comp={comp:.1f} mom={mom:.1f} chip={chip:.1f}"
        return False, ""

    def should_exit(self, code, position, scores, ohlcv):
        cost = position["avg_cost"]
        price = ohlcv["close"]
        ret = (price - cost) / cost if cost else 0
        ctx = ohlcv.get("market_context") or {}
        top_theme = ctx.get("top_theme")
        top_score = _safe_float(ctx.get("top_theme_score"), 50)
        market_bias = _safe_float(ctx.get("market_bias_score"), 50)
        if ret <= self.params.get("stop_loss", -0.05):
            return True, f"主線策略停損 {ret:.2%}"
        if top_theme in THEME_CODE_MAP and code not in THEME_CODE_MAP[top_theme] and top_score >= 60:
            return True, f"主線輪動到 {top_theme}，原持股不在主線"
        if top_score < self.params.get("exit_theme_score", 50):
            return True, f"主線降溫 score={top_score:.1f}"
        if market_bias < self.params.get("exit_market_bias", 34):
            return True, f"盤勢轉弱 market_bias={market_bias:.1f}"
        if ret >= self.params.get("take_profit", 0.15):
            return True, f"主線策略停利 {ret:.2%}"
        return False, ""


STRATEGY_REGISTRY = {
    "MomentumBreakout": MomentumBreakout,
    "ValueReversion": ValueReversion,
    "ChipFollow": ChipFollow,
    "BalancedScoreStrategy": BalancedScoreStrategy,
    "PullbackTrendStrategy": PullbackTrendStrategy,
    "ThemeTrendStrategy": ThemeTrendStrategy,
}


def build_strategy(account_id: int, class_name: str, params: dict = None) -> BaseStrategy:
    cls = STRATEGY_REGISTRY.get(class_name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {class_name}. Available: {list(STRATEGY_REGISTRY)}")
    return cls(account_id, params)
