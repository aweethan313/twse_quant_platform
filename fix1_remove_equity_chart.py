"""fix1_remove_equity_chart.py"""
import re

with open("frontend/static/strategy_page_fix.js") as f:
    js = f.read()

print(f"原始大小: {len(js)}")

# 找 ec=new Chart(...) 整個語句（跨多行）
# 先找 ec= 的位置
idx = js.find("ec=new Chart")
if idx == -1:
    idx = js.find("ec =new Chart")
if idx == -1:
    idx = js.find("ec = new Chart")

if idx >= 0:
    # 往前找這一行的開始
    line_start = js.rfind("\n", 0, idx) + 1
    # 往後找 }); 或 });
    # 計算大括號深度
    pos = idx
    depth = 0
    in_chart = False
    end_pos = len(js)
    for i in range(idx, min(idx+5000, len(js))):
        ch = js[i]
        if ch == '{': depth += 1; in_chart = True
        elif ch == '}': depth -= 1
        if in_chart and depth == 0:
            # 找到結尾 })
            end_pos = js.find(";", i) + 1
            break

    removed = js[line_start:end_pos]
    print(f"移除 ({len(removed)} chars): {removed[:80]}...")
    js = js[:line_start] + "/* equity chart removed */\n" + js[end_pos:]
else:
    print("找不到 ec=new Chart，嘗試正則...")
    js = re.sub(r'ec\s*=\s*new Chart\([^;]+\);', '/* chart removed */', js, flags=re.DOTALL)

# 移除 ec 變數宣告
js = re.sub(r',?\s*ec\s*=\s*null\b', '', js)
js = re.sub(r'(?:let|var|const)\s+ec\s*=\s*null;?\s*\n', '', js)

# 移除對 ec 的 destroy 呼叫
js = re.sub(r'if\s*\(ec\)\s*\{?\s*ec\.destroy\(\)\s*;?\s*\}?\s*\n?', '', js)

with open("frontend/static/strategy_page_fix.js","w") as f:
    f.write(js)

print(f"修正後大小: {len(js)}")
print("✓ equity chart 移除完成")
