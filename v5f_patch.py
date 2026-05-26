"""v5f_patch.py"""
import subprocess, re

print("=== V5F Patch ===\n")

# ════════════════════════════════════
# 1. 徹底移除 strategy_page_fix.js 的 equity chart
# ════════════════════════════════════
print("Step 1: 移除 strategies equity chart...")

with open("frontend/static/strategy_page_fix.js") as f:
    js = f.read()

print(f"  原始大小: {len(js)} bytes")

# 找 equity chart 相關區塊（loadEquityChart 或類似函式）
# 用正則找所有包含 Chart 的函式
funcs_removed = []
for pattern in [
    r'async function \w*[Ee]quity\w*\([^)]*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
    r'function \w*[Cc]hart\w*\([^)]*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
    r'new Chart\([^;]+\{[^}]+\}[^}]+\}[^}]+\}\s*\)',
]:
    matches = re.findall(pattern, js, re.DOTALL)
    for m in matches:
        funcs_removed.append(m[:50])
        js = js.replace(m, '/* chart removed */')

# 移除 Chart.js 相關變數宣告
js = re.sub(r'(?:let|var|const)\s+\w*[Cc]hart\s*=\s*null\s*;?\s*\n', '', js)
js = re.sub(r'if\s*\(\w*[Cc]hart\w*\)\s*\{?\s*\w+\.\w+\(\)\s*;?\s*\}?\s*\n', '', js)

print(f"  移除後大小: {len(js)} bytes")
print(f"  移除項目: {len(funcs_removed)} 個")

with open("frontend/static/strategy_page_fix.js","w") as f:
    f.write(js)

# 也從 strategies.html 移除 Chart.js 引用
with open("frontend/templates/strategies.html") as f:
    html = f.read()

original = len(html)
# 移除 Chart.js CDN
html = re.sub(r'<script[^>]*cdnjs[^>]*chart[^>]*js[^>]*></script>\s*\n?', '', html, flags=re.IGNORECASE)
html = re.sub(r'<script[^>]*chart\.umd[^>]*></script>\s*\n?', '', html, flags=re.IGNORECASE)
# 移除 equity canvas
html = re.sub(r'<div[^>]*>\s*<canvas[^>]*id="[^"]*equity[^"]*"[^>]*></canvas>\s*</div>\s*\n?', '', html, flags=re.DOTALL)
html = re.sub(r'<canvas[^>]*id="[^"]*equity[^"]*"[^>]*></canvas>\s*\n?', '', html)
# 移除 equity chart 容器 div
html = re.sub(r'<div[^>]*equity[_-]?chart[^>]*>.*?</div>', '', html, flags=re.DOTALL|re.IGNORECASE)

with open("frontend/templates/strategies.html","w") as f:
    f.write(html)
print(f"  strategies.html: {original} → {len(html)} bytes")


# ════════════════════════════════════
# 2. V3 大盤環境加入台股指數點數顯示
# ════════════════════════════════════
print("\nStep 2: V3 加入台股指數點數...")

with open("frontend/templates/v3_dashboard.html") as f:
    c = f.read()

# 修 loadMarket 加入 taiex/台指期 顯示
old_twse_row = "row('0050', `<span class=\"font-mono font-bold\">${twse.close||'—'}</span> <span style=\"color:${twseColor}\">${(twse.change_pct||0)>=0?'+':''}${(twse.change_pct||0).toFixed(2)}%</span>`) +"

new_twse_rows = """row('0050', `<span class="font-mono font-bold">${twse.close||'—'}</span> <span style="color:${twseColor}">${(twse.change_pct||0)>=0?'+':''}${(twse.change_pct||0).toFixed(2)}%</span>`) +
      (enh.taiex_close ? row('加權指數', `<span class="font-mono font-bold text-accent">${Math.round(enh.taiex_close).toLocaleString()}</span> <span style="color:${(enh.taiex_change||0)>=0?'#ff6b6b':'#51cf66'}">${(enh.taiex_change||0)>=0?'+':''}${Math.round(enh.taiex_change||0)}</span>`) : '') +
      (enh.tw_futures_close ? row('台指期', `<span class="font-mono font-bold">${Math.round(enh.tw_futures_close).toLocaleString()}</span> <span style="color:${(enh.tw_futures_change||0)>=0?'#ff6b6b':'#51cf66'}">${(enh.tw_futures_change||0)>=0?'+':''}${Math.round(enh.tw_futures_change||0)}</span>`) : '') +"""

if old_twse_row in c:
    c = c.replace(old_twse_row, new_twse_rows)
    print("  ✓ 台股指數/台指期加入 V3")
else:
    print("  - 找不到，略過")

# 夜盤摘要加入美光
old_ov_row = "row('夜盤情緒', `<span>${ovIcon}</span> <span style=\"color:${ovColor}\">${enh.summary||ov.summary||'—'}</span>`)"
new_ov_row = """row('夜盤情緒', `<span>${ovIcon}</span> <span style="color:${ovColor}" class="text-xs">${enh.summary||ov.summary||'—'}</span>`) +
      (enh.mu_ret ? row('美光 MU', `<span class="font-mono" style="color:${(enh.mu_ret||0)>=0?'#ff6b6b':'#51cf66'}">${(enh.mu_ret||0)>=0?'+':''}${(enh.mu_ret||0).toFixed(2)}%</span> <span class="text-xs text-gray-500">→ 影響記憶體/DRAM 族群</span>`) : '')"""

if old_ov_row in c:
    c = c.replace(old_ov_row, new_ov_row)
    print("  ✓ 美光 MU 顯示加入 V3")

with open("frontend/templates/v3_dashboard.html","w") as f:
    f.write(c)


# ════════════════════════════════════
# 3. 確認 overnight API 回傳台股點數
# ════════════════════════════════════
print("\nStep 3: 更新 overnight-enhanced API...")

with open("main.py") as f:
    mc = f.read()

if "/api/v2/overnight-enhanced" in mc:
    # 修改讓它使用最新的 cache 資料（含台股指數）
    old_enh = '''        return {
            "date": str(row[0]) if row else None,
            "overnight_score": float(row[2] or 50) if row else 50,
            "summary": us_summary,
            "market_regime": row[5] if row else "—",
            "twse_proxy": {
                "code": "0050",
                "date": str(idx_row[0]) if idx_row else None,
                "close": float(idx_row[1] or 0) if idx_row else 0,
                "change_pct": float(idx_row[2] or 0) if idx_row else 0,
            },
            "breadth": {
                "up": int(mkt[0] or 0) if mkt else 0,
                "down": int(mkt[1] or 0) if mkt else 0,
                "avg_change": float(mkt[2] or 0) if mkt else 0,
                "total_value_b": round(float(mkt[3] or 0)/1e8, 0) if mkt else 0,
            }
        }'''

    new_enh = '''        # 嘗試從 cache 取台股指數
        import json as _json
        from pathlib import Path as _Path
        taiex_close = taiex_change = tw_fut_close = tw_fut_change = mu_ret = None
        cache_f = _Path("data/overnight_cache.json")
        if cache_f.exists():
            try:
                cache = _json.loads(cache_f.read_text())
                bias = cache.get("bias", {})
                taiex_close = bias.get("taiex_close")
                taiex_change = bias.get("taiex_change")
                tw_fut_close = bias.get("tw_futures_close")
                tw_fut_change = bias.get("tw_futures_change")
                mu_ret = bias.get("mu_ret", 0)
            except: pass

        return {
            "date": str(row[0]) if row else None,
            "overnight_score": float(row[2] or 50) if row else 50,
            "summary": us_summary,
            "market_regime": row[5] if row else "—",
            "taiex_close": taiex_close,
            "taiex_change": taiex_change,
            "tw_futures_close": tw_fut_close,
            "tw_futures_change": tw_fut_change,
            "mu_ret": mu_ret,
            "twse_proxy": {
                "code": "0050",
                "date": str(idx_row[0]) if idx_row else None,
                "close": float(idx_row[1] or 0) if idx_row else 0,
                "change_pct": float(idx_row[2] or 0) if idx_row else 0,
            },
            "breadth": {
                "up": int(mkt[0] or 0) if mkt else 0,
                "down": int(mkt[1] or 0) if mkt else 0,
                "avg_change": float(mkt[2] or 0) if mkt else 0,
                "total_value_b": round(float(mkt[3] or 0)/1e8, 0) if mkt else 0,
            }
        }'''

    if old_enh in mc:
        mc = mc.replace(old_enh, new_enh)
        print("  ✓ overnight-enhanced 加入台股指數")
    else:
        print("  - overnight-enhanced 未找到，略過")

    with open("main.py","w") as f:
        f.write(mc)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("  ✓ main.py 語法正確" if r.returncode==0 else "  ❌ "+r.stderr.decode())

print("\n=== V5F Patch 完成 ===")
