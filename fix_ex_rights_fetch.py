path = 'backend/utils/twse_client.py'
with open(path) as f:
    c = f.read()
if 'fetch_ex_rights' in c:
    print("✓ 已修，跳過"); raise SystemExit

anchor = '''    @staticmethod
    def _roc_to_ad(roc_str: str) -> str:'''

insert = '''    # ── 除權除息（TWT48U，含未來預告）──────────────────────────────
    def fetch_ex_rights(self, start_date: date, end_date: date) -> Optional[pd.DataFrame]:
        """
        TWSE 除權息計算結果/預告。回傳 code, ex_date, action_type, cash_dividend(可能 None=待公告),
        stock_dividend_ratio。金額待公告者需每日重抓更新。
        """
        data = self._get(
            "https://www.twse.com.tw/rwd/zh/exRight/TWT48U",
            {"response": "json",
             "startDate": start_date.strftime("%Y%m%d"),
             "endDate": end_date.strftime("%Y%m%d")})
        if not data or data.get("stat") != "OK":
            return None
        rows = data.get("data") or []
        if not rows:
            return pd.DataFrame()

        def _roc_date(s):
            # '115年07月21日' → date(2026,7,21)
            try:
                import re
                m = re.match(r"(\\d+)年(\\d+)月(\\d+)日", str(s))
                return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
            except Exception:
                return None

        def _numeric(s):
            try:
                v = float(str(s).replace(",", "").strip())
                return v
            except Exception:
                return None  # '待公告' 等 HTML 文字 → None

        recs = []
        for r in rows:
            ex_d = _roc_date(r[0])
            if not ex_d:
                continue
            recs.append({
                "code": str(r[1]).strip(),
                "ex_date": str(ex_d),
                "action_type": str(r[3]).strip(),          # 息 / 權 / 權息
                "stock_dividend_ratio": _numeric(r[4]),     # 無償配股率
                "cash_dividend": _numeric(r[7]),            # 現金股利，待公告=None
            })
        return pd.DataFrame(recs)

    @staticmethod
    def _roc_to_ad(roc_str: str) -> str:'''

if anchor in c:
    with open(path, 'w') as f:
        f.write(c.replace(anchor, insert, 1))
    print("✓ twse_client 已加 fetch_ex_rights")
else:
    print("❌ 錨點失敗")
