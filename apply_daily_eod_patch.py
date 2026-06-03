"""
在 backend/collectors/daily_eod.py 的 _collect_ohlcv 加入假日 stale 防護。
重複執行不會重複加（idempotent）。
用法：python3 apply_daily_eod_patch.py
"""
import re
path = 'backend/collectors/daily_eod.py'
with open(path) as f:
    content = f.read()

if 'is_fetched_data_stale' in content:
    print("✓ 已經套用過，跳過")
else:
    old = '''    if df is None or df.empty:
        logger.warning(f"[EOD] OHLCV 無資料 {trade_date}（可能為假日）")
        return

    _upsert_stock_meta_from_daily_df(db, df)'''
    new = '''    if df is None or df.empty:
        logger.warning(f"[EOD] OHLCV 無資料 {trade_date}（可能為假日）")
        return

    # 假日污染防護：STOCK_DAY_ALL 在假日會回傳「最近交易日」的舊資料，
    # 若指標股收盤與資料庫最新交易日完全相同，代表今天非交易日，跳過寫入。
    try:
        from backend.utils.trading_day import is_fetched_data_stale
        if is_fetched_data_stale(df, trade_date, db):
            logger.warning(f"[EOD] {trade_date} 判定為非交易日（假日舊資料），跳過 OHLCV 寫入")
            return
    except Exception as e:
        logger.warning(f"[EOD] 假日防護檢查失敗（放行）：{e}")

    _upsert_stock_meta_from_daily_df(db, df)'''
    if old in content:
        content = content.replace(old, new)
        with open(path, 'w') as f:
            f.write(content)
        print("✓ daily_eod.py 已套用假日防護 patch")
    else:
        print("❌ 找不到目標位置，請貼 _collect_ohlcv 內容給 Claude 確認")
