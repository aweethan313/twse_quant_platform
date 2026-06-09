"""
前端顯示 00981A 買進持有基準線。附加式：壞了也不影響現有 0050 線與 7 帳戶。
  1. main.py 加 /api/benchmark/00981A 端點
  2. competition.html 加一個 00981A 顯示卡 + JS fetch
idempotent。用法：python3 add_00981a_frontend.py
"""
# === 1. main.py 加 API 端點 ===
mp = 'main.py'
with open(mp) as f:
    mc = f.read()
if '/api/benchmark/00981A' in mc:
    print("✓ main.py 已有 00981A 端點，跳過")
else:
    anchor = '    return get_benchmark_equity(start_date=start_date, end_date=end_date)'
    add = anchor + '''


@app.get("/api/benchmark/00981A")
def api_benchmark_00981a(start_date: str = None, end_date: str = None):
    """00981A Buy and Hold Benchmark"""
    from backend.v5.benchmark import get_benchmark_equity
    return get_benchmark_equity(start_date=start_date, end_date=end_date, benchmark_code="00981A")'''
    if anchor in mc:
        mc = mc.replace(anchor, add, 1)
        with open(mp, 'w') as f:
            f.write(mc)
        print("✓ main.py 已加 /api/benchmark/00981A 端點")
    else:
        print("⚠️ main.py 找不到 0050 端點錨點，請貼 1899 行附近給 Claude")

# === 2. competition.html 加顯示卡 + JS ===
cp = 'frontend/templates/competition.html'
with open(cp) as f:
    cc = f.read()
if 'bench_row_981' in cc:
    print("✓ competition.html 已 patch，跳過")
else:
    # 2a. 在 V5 策略帳戶排行前面，插入 00981A 卡
    html_anchor = '<!-- V5 策略帳戶排行 -->'
    html_add = '''<!-- 00981A 買進持有基準 -->
  <div class="s-card mb-4">
    <div class="text-xs text-gray-500 font-semibold uppercase mb-2">📊 00981A Benchmark（All in 買進持有）</div>
    <div id="bench_row_981" class="text-xs text-gray-400">載入中...</div>
  </div>

  ''' + html_anchor
    if html_anchor in cc:
        cc = cc.replace(html_anchor, html_add, 1)
        print("✓ competition.html 已加 00981A 顯示卡")
    else:
        print("⚠️ competition.html 找不到 HTML 錨點")

    # 2b. 在「取各帳戶 metrics」前插入 00981A fetch
    js_anchor = '// 取各帳戶 metrics'
    js_add = '''// 取 00981A benchmark（all in 買進持有對照）
    try {
      const b981 = await fetch('/api/benchmark/00981A').then(r=>r.json()).catch(()=>null);
      const b981Last = Array.isArray(b981)?b981.slice(-1)[0]:b981;
      const b981Ret = parseFloat(b981Last?.cumulative_return ?? 0);
      setHTML('bench_row_981',
        `<span class="font-mono text-blue-400 text-lg font-bold">${b981Ret>=0?'+':''}${b981Ret.toFixed(2)}%</span>
         <span class="ml-2">累積報酬（全壓 00981A）</span>`);
    } catch(e) { setHTML('bench_row_981', '無資料'); }

    ''' + js_anchor
    if js_anchor in cc:
        cc = cc.replace(js_anchor, js_add, 1)
        print("✓ competition.html 已加 00981A fetch JS")
    else:
        print("⚠️ competition.html 找不到 JS 錨點")

    with open(cp, 'w') as f:
        f.write(cc)

print("\n前端完成。重啟 uvicorn 後，Cmd+Shift+R 重新整理排行榜即可看到 00981A 那條線。")
