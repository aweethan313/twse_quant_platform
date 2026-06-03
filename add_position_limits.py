"""
為 paper 交易引擎加入兩層真實限制：
  1. 單檔上限（佔總資產比例）：一般股 8%、核心股 15%
  2. 流動性過濾：買入金額 <= 當日成交額(value) 的 0.1%；連最小量都買不到則 SKIP
取「比例上限」與「流動性上限」較小值當實際可買金額。
idempotent。用法：python3 add_position_limits.py
"""
path = 'backend/v5/paper_engine.py'
with open(path) as f:
    c = f.read()

if 'POSITION_LIMIT_PCT' in c:
    print("✓ 已加入部位限制，跳過")
    raise SystemExit

# 1. 加常數（接在 MIN_FEE 後）
old_const = "MIN_FEE    = 20                  # 最低手續費"
new_const = """MIN_FEE    = 20                  # 最低手續費

# ── 部位 / 流動性限制 ──
POSITION_LIMIT_PCT      = 0.08   # 一般股：單檔最多佔總資產 8%
CORE_POSITION_LIMIT_PCT = 0.15   # 核心股：單檔最多佔總資產 15%
LIQUIDITY_PCT           = 0.001  # 買入金額 <= 當日成交額的 0.1%
CORE_STOCKS = {"2330", "2454", "2317", "0050", "2308", "2382", "3711"}  # 核心大型股"""
c = c.replace(old_const, new_const, 1)

# 2. 改買入股數計算：加入比例上限 + 流動性上限
old_buy = """            if action == \"BUY\":
                fill_price = round(base_price * (1 + SLIP_BUY), 2)
                shares_int = int(shares or 0)
                if shares_int <= 0:
                    # 自動計算股數
                    max_amount = cash * 0.20
                    shares_int = max(1, int(max_amount / fill_price))

                gross = fill_price * shares_int"""

new_buy = """            if action == \"BUY\":
                fill_price = round(base_price * (1 + SLIP_BUY), 2)

                # 取總資產當基準（cash + 持倉市值的近似：用 initial_cash 較穩健）
                total_equity_base = float(acct[1] or 200000) if acct else 200000

                # (a) 比例上限：核心股 15%、一般股 8%
                limit_pct = CORE_POSITION_LIMIT_PCT if code in CORE_STOCKS else POSITION_LIMIT_PCT
                cap_by_pct = total_equity_base * limit_pct

                # (b) 流動性上限：當日成交額的 0.1%
                _vrow = db.execute(text(
                    "SELECT value FROM ohlcv_daily WHERE code=:c AND trade_date<=:d "
                    "ORDER BY trade_date DESC LIMIT 1"
                ), {"c": code, "d": str(exec_date)}).fetchone()
                day_value = float(_vrow[0]) if _vrow and _vrow[0] else 0
                cap_by_liq = day_value * LIQUIDITY_PCT if day_value > 0 else cap_by_pct

                # 實際可買金額 = 兩者較小，但不超過現金
                max_amount = min(cap_by_pct, cap_by_liq, cash)

                shares_int = int(shares or 0)
                if shares_int <= 0:
                    shares_int = int(max_amount / fill_price) if fill_price > 0 else 0
                else:
                    # 即使決策有指定股數，也不得超過上限
                    shares_int = min(shares_int, int(max_amount / fill_price) if fill_price > 0 else 0)

                # 流動性太低 → 買不到一張的額度就 SKIP
                if shares_int <= 0:
                    logger.debug(f\"[PAPER] A{aid} {code} SKIP 流動性/部位上限不足（成交額={day_value:.0f}）\")
                    continue

                gross = fill_price * shares_int"""

c = c.replace(old_buy, new_buy, 1)

with open(path, 'w') as f:
    f.write(c)
print("✓ 已加入單檔比例上限（一般8%/核心15%）+ 流動性過濾（成交額0.1%）")
