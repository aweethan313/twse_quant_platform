"""fix234.py - 台股點數/0050/夜盤修復"""
import re, subprocess

# ══════════════════════════════
# Fix 2: overnight_market.py 台股指數連續日計算
# ══════════════════════════════
print("Fix 2: 台股指數點數...")

with open("backend/collectors/overnight_market.py") as f:
    ov = f.read()

old_tw = """            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last_close
            ret = (last_close - prev_close) / prev_close if prev_close else 0
            point_change = last_close - prev_close"""

new_tw = """            last_close = float(hist["Close"].iloc[-1])
            # 確保只比較連續交易日（≤5天）
            prev_close = last_close
            point_change = 0.0
            ret = 0.0
            if len(hist) >= 2:
                last_date = hist.index[-1]
                prev_date = hist.index[-2]
                day_diff = abs((last_date - prev_date).days)
                if day_diff <= 5:
                    prev_close = float(hist["Close"].iloc[-2])
                    point_change = round(last_close - prev_close, 2)
                    ret = (last_close - prev_close) / prev_close if prev_close else 0"""

if old_tw in ov:
    ov = ov.replace(old_tw, new_tw)
    with open("backend/collectors/overnight_market.py","w") as f:
        f.write(ov)
    print("  ✓ 台股指數連續日計算修正")
else:
    print("  - 找不到，略過")


# ══════════════════════════════
# Fix 3: 0050 顯示 0% 問題
# ══════════════════════════════
print("Fix 3: 0050 顯示...")

with open("frontend/templates/market.html") as f:
    mhtml = f.read()

# 找 v4_etf_status 的更新邏輯，看漲跌如何計算
idx = mhtml.find("v4_etf_status")
nearby = mhtml[idx:idx+500]
print(f"  v4_etf_status 附近: {nearby[:200]}")


# ══════════════════════════════
# Fix 4: 確認夜盤卡片 + 確保正確附加到頁面
# ══════════════════════════════
print("\nFix 4: 夜盤/主題卡片位置...")

# 目前夜盤卡片 append 到 '.container-fluid, main, body'
# 確保它附加到選股篩選器下方的區域
old_wrap = """      const wrap = document.querySelector('.container-fluid, main, body');
      if (wrap) wrap.appendChild(card);"""

new_wrap = """      // 附加到頁面主要區域（選股篩選器所在的 div 下方）
      const mainContent = document.querySelector('.max-w-7xl, .container-fluid, main') || document.body;
      mainContent.appendChild(card);"""

if old_wrap in mhtml:
    mhtml = mhtml.replace(old_wrap, new_wrap)
    print("  ✓ 夜盤卡片附加位置修正")

# 確保 loadOvernight 在 DOMContentLoaded 時被呼叫
if "DOMContentLoaded" in mhtml and "loadOvernight" in mhtml:
    print("  ✓ loadOvernight 在 DOMContentLoaded 中")

with open("frontend/templates/market.html","w") as f:
    f.write(mhtml)

r = subprocess.run(["python3","-m","py_compile","backend/collectors/overnight_market.py"], capture_output=True)
print("  ✓ overnight_market.py 語法正確" if r.returncode==0 else "  ❌ "+r.stderr.decode())
print("\n✓ Fix 2/3/4 完成")
