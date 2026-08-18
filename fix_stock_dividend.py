"""
工程債 #4:配股(股票股利)自動處理

背景:
  除權息中的「配股」部分原本僅記警告,需人工調整持股數。
  但 8/3 起淨值改為「從 paper_fills 重播」計算,
  **光改 positions 表無效** —— 重播不會讀它。

修法:
  在除權日寫一筆「零成本的合成買進」進 paper_fills:
    action='BUY', shares=配股數, fill_price=0, fee=0, tax=0,
    gross_amount=0, net_amount=0, note='stock_dividend'
  重播時持股增加、總成本不變 → 平均成本自動攤薄 → 淨值正確反映配股。

配股數計算:
  new_shares = int(除權日前一交易日持股 × stock_dividend_ratio)
  (捨去小數,零股不配)
  單位已驗證:5880 配股率 0.025 = 每股配 0.025 股
  (8/10 收 26.1、配息 0.8 → 理論參考價 (26.1-0.8)/1.025 = 24.68,
   實際 8/11 收 24.0,吻合)

冪等:以 (account_id, code, execution_date, note) 判重,重跑不會重複配發。

用法:python3 fix_stock_dividend.py
"""
path = 'backend/v5/dividends.py'
with open(path) as f:
    src = f.read()

if 'STOCK_DIVIDEND_FILL' in src:
    print("✓ 已修,跳過")
    raise SystemExit

old = '''                if stock_ratio and float(stock_ratio) > 0:
                    logger.warning(f"[DIV] A{aid} {code} {ex_date} 含配股(率={stock_ratio}),股數調整需人工處理")'''

new = '''                # STOCK_DIVIDEND_FILL:配股寫成「零成本合成買進」,
                # 使 paper_fills 重播能正確反映股數增加與成本攤薄
                if stock_ratio and float(stock_ratio) > 0:
                    _new_sh = int(sh * float(stock_ratio))
                    if _new_sh > 0:
                        _dup_sd = db.execute(text("""
                            SELECT 1 FROM paper_fills
                            WHERE account_id=:a AND code=:c
                              AND execution_date=:d AND note='stock_dividend'
                        """), {"a": aid, "c": code, "d": str(ex_date)}).scalar()
                        if not _dup_sd:
                            db.execute(text("""
                                INSERT INTO paper_fills
                                    (account_id, strategy_name, signal_date, execution_date,
                                     code, action, shares, fill_price, fill_source,
                                     fee, tax, gross_amount, net_amount, note, no_lookahead_pass)
                                VALUES
                                    (:a, 'stock_dividend', :d, :d,
                                     :c, 'BUY', :sh, 0, 'stock_dividend',
                                     0, 0, 0, 0, 'stock_dividend', 1)
                            """), {"a": aid, "c": code, "d": str(ex_date), "sh": _new_sh})
                            logger.success(
                                f"[DIV] A{aid} {code} 配股{ex_date} "
                                f"{sh:.0f}股 × {stock_ratio} = +{_new_sh}股 已入帳")
                    else:
                        logger.info(
                            f"[DIV] A{aid} {code} 配股{ex_date} 不足1股({sh:.0f}×{stock_ratio}),略過")'''

if old in src:
    with open(path, 'w') as f:
        f.write(src.replace(old, new, 1))
    print("✓ 配股自動入帳已加入")
    print("\n請執行驗證:")
    print("  grep -c 'STOCK_DIVIDEND_FILL' backend/v5/dividends.py")
    print("  python3 -m py_compile backend/v5/dividends.py")
else:
    print("❌ 錨點失敗,請把 dividends.py 的配股警告那兩行貼給 Claude")
