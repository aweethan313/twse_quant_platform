"""
工程債 #2:決策去重保護(先刪後插,使重跑冪等)

背景(2026-08-13):
  手動補跑 A8 決策時執行了兩次,產生 40 筆重複決策(每檔兩次)。
  若未發現,隔日會下單雙倍金額(40 萬 > 20 萬本金),一半失敗。

修法:
  每個帳戶開始產生決策前,先刪除該帳戶該訊號日的「未成交」決策。
  已成交的(有對應 paper_fills.plan_id)保留,避免破壞成交紀錄的對應關係。

效果:
  - 正常每日運作:無影響(每天只跑一次,沒有東西可刪)
  - 手動補跑 / 回填重跑:天然冪等,不再累積重複

用法:python3 fix_decision_dedup.py
"""
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    src = f.read()

if 'DECISION_DEDUP' in src:
    print("✓ 已修,跳過")
    raise SystemExit

old = '''        for cfg_row in configs:
            cfg = dict(zip(col_names, cfg_row))'''

new = '''        for cfg_row in configs:
            cfg = dict(zip(col_names, cfg_row))
            # DECISION_DEDUP:先刪後插,使重跑冪等
            # (2026-08-13:手動補跑執行兩次,產生 40 筆重複決策)
            # 只刪未成交的,已成交的保留以維持 paper_fills.plan_id 對應
            db.execute(text("""
                DELETE FROM strategy_decision_logs
                WHERE account_id = :aid AND signal_date = :sd
                  AND id NOT IN (
                      SELECT COALESCE(plan_id, -1) FROM paper_fills
                      WHERE account_id = :aid
                  )
            """), {"aid": cfg["account_id"], "sd": str(signal_date)})'''

if old in src:
    with open(path, 'w') as f:
        f.write(src.replace(old, new, 1))
    print("✓ 決策去重保護已加入")
    print("\n請執行驗證:")
    print("  grep -c 'DECISION_DEDUP' backend/v5/decision_engine.py")
    print("  python3 -m py_compile backend/v5/decision_engine.py")
else:
    print("❌ 錨點失敗,請把 decision_engine.py 的 for cfg_row 那兩行貼給 Claude")
