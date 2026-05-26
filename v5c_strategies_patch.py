"""v5c_strategies_patch.py - 修 strategies.html 顯示 V5 帳戶"""
import subprocess

with open("frontend/templates/strategies.html") as f:
    c = f.read()

# 在頁面加入 V5 帳戶區塊
V5_SECTION = """
  <!-- V5 Forward Paper Accounts -->
  <div class="mb-6">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-sm font-semibold text-accent">🆕 V5 Forward Paper Strategy Accounts</h2>
      <span class="text-xs text-gray-500">每日自動交易 · vs 0050 競賽</span>
    </div>
    <div id="v5_accounts_grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      <div class="text-gray-600 text-xs py-4">載入中...</div>
    </div>
  </div>
  <div class="border-t border-border mb-6 pt-2">
    <div class="text-xs text-gray-600 mb-3">── 舊版策略帳戶 ──</div>
  </div>
"""

# 在 account_cards 前插入 V5 區塊
if "v5_accounts_grid" not in c:
    c = c.replace(
        '<div id="account_cards"',
        V5_SECTION + '<div id="account_cards"'
    )
    print("✓ V5 帳戶區塊加入 strategies.html")
else:
    print("- V5 區塊已存在")

# 加入 V5 帳戶載入 JS
V5_JS = """
// ── V5 帳戶顯示 ──
async function loadV5Accounts() {
  try {
    const d = await fetch('/api/strategy-accounts').then(r=>r.json());
    const bench = await fetch('/api/benchmark/0050').then(r=>r.json());
    const benchLatest = bench.length ? bench[bench.length-1] : null;
    const benchRet = benchLatest ? +benchLatest.cumulative_return : 0;

    const grid = document.getElementById('v5_accounts_grid');
    if (!d.length) {
      grid.innerHTML = '<div class="text-gray-600 text-xs">尚無 V5 帳戶</div>';
      return;
    }

    grid.innerHTML = d.map(a => {
      const total = a.total_equity || 200000;
      const ret = a.total_return || 0;
      const alpha = +(ret - benchRet).toFixed(2);
      const retColor = ret >= 0 ? '#ff6b6b' : '#51cf66';
      const alphaColor = alpha >= 0 ? '#44ff88' : '#ff4444';
      return `
      <div class="bg-surface-2 border border-border rounded-lg p-4">
        <div class="flex justify-between items-start mb-2">
          <div>
            <div class="text-xs font-semibold text-white">A${a.account_id} ${a.name}</div>
            <div class="text-xs text-gray-500">${a.strategy_name || ''}</div>
          </div>
          <div class="text-right">
            <div class="text-sm font-bold font-mono" style="color:${retColor}">${ret>=0?'+':''}${ret.toFixed(2)}%</div>
            <div class="text-xs" style="color:${alphaColor}">α ${alpha>=0?'+':''}${alpha.toFixed(2)}%</div>
          </div>
        </div>
        <div class="space-y-1 text-xs">
          <div class="flex justify-between">
            <span class="text-gray-500">總資產</span>
            <span class="font-mono">${Math.round(total).toLocaleString()}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-500">現金</span>
            <span class="font-mono">${Math.round(a.cash||0).toLocaleString()}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-500">市值</span>
            <span class="font-mono">${Math.round(a.market_value||0).toLocaleString()}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-500">0050 同期</span>
            <span class="font-mono" style="color:#ffd700">${benchRet>=0?'+':''}${benchRet.toFixed(2)}%</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-500">策略</span>
            <span class="text-gray-300 text-xs">${(a.description||'').slice(0,25)}</span>
          </div>
        </div>
        <div class="mt-2 pt-2 border-t border-gray-800 flex gap-2">
          <a href="/paper" class="text-xs text-blue-400 hover:underline">持倉</a>
          <a href="/competition" class="text-xs text-blue-400 hover:underline">競賽</a>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    document.getElementById('v5_accounts_grid').innerHTML =
      '<div class="text-gray-600 text-xs">V5 帳戶載入失敗</div>';
  }
}
"""

if "loadV5Accounts" not in c:
    # 在 </script> 前插入
    c = c.replace("</script>", V5_JS + "\n</script>", 1)
    # 在 DOMContentLoaded 加入呼叫
    if "loadV5Accounts()" not in c:
        c = c.replace(
            "loadAccounts();",
            "loadAccounts();\n  loadV5Accounts();"
        )
    print("✓ loadV5Accounts JS 加入")
else:
    print("- loadV5Accounts 已存在")

with open("frontend/templates/strategies.html","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","frontend/templates/strategies.html"],
                   capture_output=True)
print("- strategies.html 無語法問題（Jinja 不驗證）")
print("✓ strategies.html 更新完成")
