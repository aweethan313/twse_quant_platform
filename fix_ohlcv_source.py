path = 'backend/utils/twse_client.py'
with open(path) as f:
    c = f.read()

if 'fetch_mi_index' in c:
    print("✓ 已修，跳過")
    raise SystemExit

old = '''    # ── 日K (TWSE STOCK_DAY_ALL) ─────────────────────────────────────
    def fetch_daily_all(self, trade_date: date) -> Optional[pd.DataFrame]:'''

new = '''    # ── 日K 主源 MI_INDEX（支援歷史日期）＋備援 STOCK_DAY_ALL ────────
    def fetch_daily_all(self, trade_date: date) -> Optional[pd.DataFrame]:
        """主源 MI_INDEX → 失敗改用 STOCK_DAY_ALL 備援。"""
        df = self.fetch_mi_index(trade_date)
        if df is not None and len(df) > 800:
            logger.info(f"[OHLCV] {trade_date} 使用 MI_INDEX（{len(df)} 檔）")
            return df
        logger.warning(f"[OHLCV] {trade_date} MI_INDEX 無資料，改用 STOCK_DAY_ALL 備援")
        return self._fetch_stock_day_all(trade_date)

    def fetch_mi_index(self, trade_date: date) -> Optional[pd.DataFrame]:
        """TWSE 全市場收盤行情 MI_INDEX（可指定歷史日期）"""
        date_str = trade_date.strftime("%Y%m%d")
        url = f"{settings.TWSE_BASE_URL}/MI_INDEX"
        data = self._get(url, {"response": "json", "date": date_str, "type": "ALLBUT0999"})
        if not data or data.get("stat") != "OK":
            return None
        returned_date = str(data.get("date") or "").replace("-", "").replace("/", "")
        if returned_date and returned_date != date_str:
            logger.warning(f"[MI_INDEX] returned_date={returned_date} != requested={date_str}; skip")
            return None
        fields, rows = None, None
        for t in (data.get("tables") or []):
            f = t.get("fields") or []
            if "證券代號" in f and "收盤價" in f:
                fields, rows = f, (t.get("data") or [])
                break
        if fields is None:
            for i in range(1, 12):
                f = data.get(f"fields{i}") or []
                if "證券代號" in f and "收盤價" in f:
                    fields, rows = f, (data.get(f"data{i}") or [])
                    break
        if not fields or not rows:
            logger.warning(f"[MI_INDEX] {trade_date} 找不到個股行情表")
            return None
        idx = {name: i for i, name in enumerate(fields)}

        def _num(cell):
            try:
                return float(str(cell).replace(",", "").strip())
            except Exception:
                return None

        def _sign(cell):
            return -1.0 if "-" in str(cell) else 1.0

        recs = []
        for r in rows:
            try:
                chg_mag = _num(r[idx["漲跌價差"]]) if "漲跌價差" in idx else None
                sign = _sign(r[idx["漲跌(+/-)"]]) if "漲跌(+/-)" in idx else 1.0
                recs.append({
                    "code":     str(r[idx["證券代號"]]).strip(),
                    "name":     str(r[idx["證券名稱"]]).strip(),
                    "volume":   _num(r[idx["成交股數"]]),
                    "value":    _num(r[idx["成交金額"]]),
                    "open":     _num(r[idx["開盤價"]]),
                    "high":     _num(r[idx["最高價"]]),
                    "low":      _num(r[idx["最低價"]]),
                    "close":    _num(r[idx["收盤價"]]),
                    "change":   (chg_mag * sign) if chg_mag is not None else None,
                    "tx_count": _num(r[idx["成交筆數"]]) if "成交筆數" in idx else None,
                })
            except Exception:
                continue
        if not recs:
            return None
        df = pd.DataFrame(recs)
        df["trade_date"] = trade_date
        df["change_pct"] = df["change"] / (df["close"] - df["change"]) * 100
        return df

    # ── 備援：STOCK_DAY_ALL（僅回當日快照，休市日會回舊資料）─────────
    def _fetch_stock_day_all(self, trade_date: date) -> Optional[pd.DataFrame]:'''

if old in c:
    c = c.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ twse_client.py 改造完成")
else:
    print("❌ 找不到錨點")
