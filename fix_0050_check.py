"""fix_0050.py - 修正 0050 顯示 0% 問題"""

with open("frontend/templates/market.html") as f:
    c = f.read()

# 找 v4_etf_status 的設定邏輯
import re

# 找整個 0050 更新區塊
idx = c.find("v4_etf_status")
start = c.rfind("try {", 0, idx)
end = c.find("} catch", idx)
block = c[start:end+20]
print("0050 區塊:")
print(block[:600])
print("---")

# 找 change_pct 計算方式
if "change_pct" in block:
    print("✓ 有 change_pct")
elif "ret" in block:
    print("有 ret 但可能計算錯誤")
    # 找計算方式
    idx2 = block.find("ret")
    print("ret 計算:", block[max(0,idx2-50):idx2+100])
