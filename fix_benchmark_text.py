"""
修正排行榜 0050 benchmark 初次載入時文字寫死「2025/1/1 起」的問題。
改成讀起始日選擇器的實際值，並讓排行榜初次載入就用正確 start_date。
idempotent。用法：python3 fix_benchmark_text.py
"""
path = 'frontend/templates/competition.html'
with open(path) as f:
    c = f.read()

if "const initStart = document.getElementById('bench_start_input')" in c:
    print("✓ 已修正，跳過")
else:
    # 把初次載入的 benchmark 區塊改成讀 input 值
    old = """    // 取 0050 benchmark
    const bench = await fetch('/api/benchmark/0050').then(r=>r.json()).catch(()=>null);
    const benchLast = Array.isArray(bench)?bench.slice(-1)[0]:bench;
    const benchRet = parseFloat(benchLast?.cumulative_return ?? benchLast?.data?.slice(-1)[0]?.cumulative_return ?? 0);
    setHTML('bench_row',
      `<span class="font-mono text-yellow-400 text-lg font-bold">${benchRet>=0?'+':''}${parseFloat(benchRet).toFixed(2)}%</span>
       <span class="ml-2">累積報酬（2025/1/1 起）</span>`
    );
    setHTML('bench_info', `0050 同期：${parseFloat(benchRet).toFixed(2)}%`);"""

    new = """    // 取 0050 benchmark
    const initStart = document.getElementById('bench_start_input')?.value || '2026-05-25';
    if (!globalStartDate) globalStartDate = initStart;
    const bench = await fetch('/api/benchmark/0050').then(r=>r.json()).catch(()=>null);
    const benchLast = Array.isArray(bench)?bench.slice(-1)[0]:bench;
    const benchRet = parseFloat(benchLast?.cumulative_return ?? benchLast?.data?.slice(-1)[0]?.cumulative_return ?? 0);
    setHTML('bench_row',
      `<span class="font-mono text-yellow-400 text-lg font-bold">${benchRet>=0?'+':''}${parseFloat(benchRet).toFixed(2)}%</span>
       <span class="ml-2">累積報酬（${initStart} 起）</span>`
    );
    setHTML('bench_info', `0050 同期：${parseFloat(benchRet).toFixed(2)}%`);"""

    if old in c:
        c = c.replace(old, new)
        with open(path, 'w') as f:
            f.write(c)
        print("✓ benchmark 初始文字已改成動態讀取起始日")
    else:
        print("❌ 找不到目標，可能已被改過")
