"""v5d_strategies_clean.py - strategies.html 移除 equity chart + 舊帳戶相關"""
import re

with open("frontend/templates/strategies.html") as f:
    c = f.read()

# 1. 移除 equity chart canvas（如果存在）
# 找 canvas 標籤（用於 equity chart）
canvas_patterns = [
    r'<canvas[^>]+id="[^"]*equity[^"]*"[^>]*></canvas>',
    r'<canvas[^>]+id="[^"]*chart[^"]*"[^>]*></canvas>',
    r'<div[^>]+id="[^"]*chart_area[^"]*"[^>]*>.*?</div>',
]
removed = 0
for pat in canvas_patterns:
    matches = re.findall(pat, c, re.DOTALL)
    for m in matches:
        if 'equity' in m.lower() or 'chart' in m.lower():
            c = c.replace(m, '')
            removed += 1

if removed:
    print(f"✓ 移除 {removed} 個 chart canvas")
else:
    print("- 無 chart canvas 需要移除")

# 2. 在策略帳戶卡片中移除 equity curve 圖表區塊
c = re.sub(
    r'<!-- equity curve[^>]*-->.*?</div>\s*\n',
    '',
    c, flags=re.DOTALL | re.IGNORECASE
)

# 3. 移除 Chart.js 的初始化 code（如果在 strategies.html 裡）
c = re.sub(r'new Chart\([^)]+\{[^}]+\}\)', '', c)

# 4. 確保 v5_accounts_grid 在頁面頂部
if "v5_accounts_grid" not in c:
    print("⚠️ v5_accounts_grid 不在 strategies.html")
else:
    print("✓ v5_accounts_grid 存在")

# 5. 加入風調排名說明到 V5 區塊
c = c.replace(
    '<div id="v5_accounts_grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">',
    '<div class="text-xs text-gray-600 mb-2">排名依 Alpha vs 0050 | 括號為風調分數</div>\n    <div id="v5_accounts_grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">'
)

with open("frontend/templates/strategies.html","w") as f:
    f.write(c)
print("✓ strategies.html 清理完成")
