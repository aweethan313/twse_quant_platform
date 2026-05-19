"""
backend/utils/twse_client.py  –  TWSE / MOPS HTTP 封裝
官方限流保護：每次請求後等待 REQUEST_DELAY_SEC
"""
import time
import httpx
import pandas as pd
from datetime import date, timedelta
from typing import Optional
from loguru import logger
from config.settings import settings


class TWSEClient:
    """
    封裝所有對 TWSE/MOPS Open Data 的 HTTP 請求
    所有方法回傳 pd.DataFrame，失敗回傳 None
    """

    def __init__(self):
        self.session = httpx.Client(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; QuantResearch/1.0)"}
        )
        self._last_req = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_req
        wait = settings.REQUEST_DELAY_SEC - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.time()

    def _get(self, url: str, params: dict = None) -> Optional[dict]:
        self._throttle()
        try:
            r = self.session.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"GET {url} failed: {e}")
            return None

    # ── 日K (TWSE STOCK_DAY_ALL) ─────────────────────────────────────
    def fetch_daily_all(self, trade_date: date) -> Optional[pd.DataFrame]:
        """
        抓取單日所有上市股票 OHLCV
        URL: https://www.twse.com.tw/exchangeReport/MI_INDEX
        """
        date_str = trade_date.strftime("%Y%m%d")
        url = f"{settings.TWSE_BASE_URL}/STOCK_DAY_ALL"
        data = self._get(url, {"response": "json", "date": date_str})
        if not data or data.get("stat") != "OK":
            return None
        cols = ["code", "name", "volume", "value", "open", "high", "low",
                "close", "change", "tx_count"]
        df = pd.DataFrame(data["data"], columns=cols)
        df["trade_date"] = trade_date
        for c in ["volume", "value", "open", "high", "low", "close", "change"]:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
        df["change_pct"] = df["change"] / (df["close"] - df["change"]) * 100
        return df

    # ── 個股日K (STOCK_DAY) ──────────────────────────────────────────
    def fetch_stock_month(self, code: str, year: int, month: int) -> Optional[pd.DataFrame]:
        """抓取單一股票單月日K"""
        date_str = f"{year}{month:02d}01"
        url = f"{settings.TWSE_BASE_URL}/STOCK_DAY"
        data = self._get(url, {
            "response": "json", "date": date_str,
            "stockNo": code
        })
        if not data or data.get("stat") != "OK":
            return None
        n_cols = len(data["data"][0]) if data.get("data") else 0
        base_cols = ["trade_date", "volume", "value", "open", "high", "low", "close", "change", "tx_count"]
        cols = base_cols + ["note"] * (n_cols - len(base_cols))  # 自動補齊多餘欄位
        df = pd.DataFrame(data["data"], columns=cols)
        df["code"] = code
        df["trade_date"] = pd.to_datetime(
            df["trade_date"].apply(lambda x: self._roc_to_ad(x)),
            format="%Y/%m/%d"
        ).dt.date
        for c in ["volume", "value", "open", "high", "low", "close", "change"]:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
        df["change_pct"] = df["change"] / (df["close"] - df["change"]) * 100
        return df

    @staticmethod
    def _roc_to_ad(roc_str: str) -> str:
        """民國日期 → 西元 e.g. '113/01/02' → '2024/01/02'"""
        parts = roc_str.split("/")
        return f"{int(parts[0])+1911}/{parts[1]}/{parts[2]}"

    # ── 三大法人 ─────────────────────────────────────────────────────
        # ── 三大法人 ─────────────────────────────────────────────────────
    def fetch_institutional(self, trade_date: date) -> Optional[pd.DataFrame]:
        """
        三大法人買賣超。

        重要：
        - TWSE T86 回傳單位通常是「股數」
        - 這裡統一轉成「張」寫入 chip_daily
        - 避免後面 chip_score 閾值被股數放大 1000 倍
        """
        date_str = trade_date.strftime("%Y%m%d")
        url = "https://www.twse.com.tw/fund/T86"

        data = self._get(url, {
            "response": "json",
            "date": date_str,
            "selectType": "ALLBUT0999",
        })

        if not data:
            logger.warning(f"[T86] no response: {trade_date}")
            return None

        stat = data.get("stat", "")
        rows = data.get("data", [])
        fields = data.get("fields", [])

        if stat != "OK" or not rows:
            logger.warning(f"[T86] no data: {trade_date}, stat={stat}")
            return None

        df = pd.DataFrame(rows, columns=fields[:len(rows[0])] if fields else None)

        def find_col(keywords, exclude_keywords=None):
            exclude_keywords = exclude_keywords or []
            for col in df.columns:
                col_s = str(col)
                if all(k in col_s for k in keywords) and not any(e in col_s for e in exclude_keywords):
                    return col
            return None

        def clean_number(x) -> float:
            if x is None:
                return 0.0
            s = str(x).strip()
            s = s.replace(",", "")
            s = s.replace("--", "")
            s = s.replace("X", "")
            s = s.replace("+", "")
            if s == "" or s.lower() == "nan":
                return 0.0
            try:
                return float(s)
            except ValueError:
                return 0.0

        code_col = find_col(["證券", "代號"]) or find_col(["代號"])
        name_col = find_col(["證券", "名稱"]) or find_col(["名稱"])

        foreign_col = find_col(["外資", "買賣超"], exclude_keywords=["外資自營商"])
        trust_col = find_col(["投信", "買賣超"])
        dealer_col = find_col(["自營商", "買賣超"], exclude_keywords=["自行", "避險"])

        if code_col is None:
            logger.warning(f"[T86] cannot find code column: {trade_date}, fields={fields}")
            return None

        out = pd.DataFrame()
        out["code"] = df[code_col].astype(str).str.strip()
        out["name"] = df[name_col].astype(str).str.strip() if name_col is not None else ""

        # T86 是股數，轉成張
        out["foreign_net"] = df[foreign_col].map(clean_number) / 1000.0 if foreign_col is not None else 0.0
        out["trust_net"] = df[trust_col].map(clean_number) / 1000.0 if trust_col is not None else 0.0
        out["dealer_net"] = df[dealer_col].map(clean_number) / 1000.0 if dealer_col is not None else 0.0

        out["trade_date"] = trade_date

        # 只保留正常股票代號，避免合計列、空白列混進來
        out = out[out["code"].str.match(r"^[0-9A-Z]{4,6}$", na=False)].copy()

        if out.empty:
            logger.warning(f"[T86] parsed empty dataframe: {trade_date}")
            return None

        logger.info(
            f"[T86] {trade_date} parsed {len(out)} rows | "
            f"foreign_col={foreign_col}, trust_col={trust_col}, dealer_col={dealer_col}"
        )

        return out

    # ── 融資融券 ─────────────────────────────────────────────────────
    def fetch_margin(self, trade_date: date) -> Optional[pd.DataFrame]:
        """融資融券餘額"""
        date_str = trade_date.strftime("%Y%m%d")
        url = f"{settings.TWSE_BASE_URL}/MI_MARGN"
        data = self._get(url, {"response": "json", "date": date_str, "selectType": "ALL"})
        if not data or data.get("stat") != "OK":
            return None
        df = pd.DataFrame(data.get("data", []))
        df["trade_date"] = trade_date
        return df

    # ── 月營收 (MOPS) ─────────────────────────────────────────────────
    def fetch_monthly_revenue(self, year: int, month: int) -> Optional[pd.DataFrame]:
        """
        月營收資料 - MOPS t21sc03_1
        year: 西元年
        """
        roc_year = year - 1911
        url = "https://mops.twse.com.tw/nas/t21/sii/t21sc03_1_{roc_year}_{month}_0.htm".format(
            roc_year=roc_year, month=month
        )
        self._throttle()
        try:
            r = self.session.get(url)
            tables = pd.read_html(r.text, encoding="big5")
            if not tables:
                return None
            df = tables[0]
            df["year"] = year
            df["month"] = month
            return df
        except Exception as e:
            logger.warning(f"fetch_monthly_revenue {year}/{month}: {e}")
            return None

    # ── 盤中即時（MIS）─────────────────────────────────────────────
    def fetch_intraday_snapshot(self, codes: list[str]) -> Optional[pd.DataFrame]:
        """
        盤中即時行情快照
        最多一次 50 檔，格式: 2330.tw|2317.tw|...
        """
        ex_ids = "|".join([f"s_{c}" for c in codes[:50]])
        url = f"{settings.TWSE_INTRADAY_URL}/getStockInfo.jsp"
        params = {
            "ex_ch": ex_ids,
            "json": "1",
            "delay": "0"
        }
        data = self._get(url, params)
        if not data or "msgArray" not in data:
            return None
        records = []
        for item in data["msgArray"]:
            try:
                records.append({
                    "code":    item.get("c", ""),
                    "name":    item.get("n", ""),
                    "close":   float(item.get("z", "0") or 0),
                    "open":    float(item.get("o", "0") or 0),
                    "high":    float(item.get("h", "0") or 0),
                    "low":     float(item.get("l", "0") or 0),
                    "volume":  float(item.get("v", "0") or 0),
                    "buy_vol": float(item.get("b", "0").split("_")[0] if item.get("b") else 0),
                    "sell_vol":float(item.get("a", "0").split("_")[0] if item.get("a") else 0),
                    "ts":      item.get("t", ""),
                })
            except (ValueError, TypeError):
                continue
        return pd.DataFrame(records) if records else None

    def close(self):
        self.session.close()


# 全域 singleton
twse_client = TWSEClient()
