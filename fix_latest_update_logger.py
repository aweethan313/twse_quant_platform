"""
修 latest_update.py 的兩個問題：
  1. 第102行用 logger 但檔案開頭沒 import → 加 from loguru import logger
  2. overnight 缺 yfinance → 包成「缺套件時優雅跳過」，不讓它整個流程報錯
idempotent。用法：python3 fix_latest_update_logger.py
"""
path = 'backend/services/latest_update.py'
with open(path) as f:
    c = f.read()

# 1. 開頭加 import logger（接在 from sqlalchemy import text 後）
if 'from loguru import logger' in c.split('def ')[0]:
    print("✓ 開頭已 import logger，跳過")
else:
    c = c.replace(
        "from sqlalchemy import text\n",
        "from sqlalchemy import text\nfrom loguru import logger\n",
        1
    )
    print("✓ 開頭已加入 from loguru import logger")

# 2. overnight 缺 yfinance 優雅處理
if 'yfinance 未安裝' not in c and 'def update_overnight()' in c:
    old = '''def update_overnight() -> dict[str, Any]:
    mod = importlib.import_module("backend.collectors.overnight_market")'''
    new = '''def update_overnight() -> dict[str, Any]:
    try:
        import yfinance  # noqa: F401
    except ImportError:
        logger.warning("[OVERNIGHT] yfinance 未安裝，跳過美股隔夜資料（台股策略不需要）")
        return {"ok": True, "skipped": "yfinance 未安裝"}
    mod = importlib.import_module("backend.collectors.overnight_market")'''
    if old in c:
        c = c.replace(old, new, 1)
        print("✓ overnight 改為缺 yfinance 時優雅跳過")
    else:
        print("⚠ update_overnight 結構不同，只修了 logger")

with open(path, 'w') as f:
    f.write(c)
print("\n完成。重啟 server 後按更新，daily_eod 不會再噴 logger 錯誤、overnight 會優雅跳過。")
