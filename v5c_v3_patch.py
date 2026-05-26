"""v5c_v3_patch.py - V3 dashboard 加入 V5 帳戶狀況區塊"""
import subprocess

with open("frontend/templates/v3_dashboard.html") as f:
    c = f.read()

# 在策略帳戶區塊後加入 V5 摘要
V5_V3_SECTION = """
  <!-- V5 帳戶狀況 -->
  <div class="dc-card mb-4">
    <div class="flex items-center justify-between mb-2">
      <div class="text-xs text-gray-500 font-semibold uppercase tracking-wide">🆕 V5 Forward Paper 狀況</div>
      <a href="/competition" class="text-xs text-blue-400 hover:underline">月度競賽 →</a>
    </div>
    <div id="dc_v5_accounts">載入中...</div>
  </div>
"""

if "dc_v5_accounts" not in c:
    c = c.replace(
        "  <!-- E. 今天不能做什麼 -->",
        V5_V3_SECTION + "  <!-- E. 今天不能做什麼 -->"
    )
    print("✓ V5 帳戶區塊加入 V3")
else:
    print("- V5 區塊已存在")

# 加入 loadV5Summary JS
V5_SUMMARY_JS = """
async function loadV5Summary() {
  try {
    const [accts, bench] = await Promise.all([
      fetch('/api/strategy-accounts').then(r=>r.json()),
      fetch('/api/benchmark/0050').then(r=>r.json()),
    ]);
    const benchRet = bench.length ? +bench[bench.length-1].cumulative_return : 0;
    const el = document.getElementById('dc_v5_accounts');
    if (!accts.length) { el.textContent = '尚無 V5 帳戶'; return; }
    el.innerHTML = `<table class="w-full text-xs">
      <thead><tr class="text-gray-500">
        <th class="text-left py-1">帳戶</th>
        <th class="text-right">總資產</th>
        <th class="text-right">累積報酬</th>
        <th class="text-right">Alpha</th>
      </tr></thead>
      <tbody>
      ${accts.map(a => {
        const ret = +(a.total_return||0);
        const alpha = +(ret - benchRet).toFixed(2);
        return `<tr class="border-t border-gray-800">
          <td class="py-1">A${a.account_id} ${a.name.replace('A'+a.account_id+' ','')}</td>
          <td class="text-right font-mono">${Math.round(a.total_equity||200000).toLocaleString()}</td>
          <td class="text-right font-mono ${ret>=0?'text-up':'text-dn'}">${ret>=0?'+':''}${ret.toFixed(2)}%</td>
          <td class="text-right font-mono ${alpha>=0?'text-green-400':'text-red-400'}">${alpha>=0?'+':''}${alpha.toFixed(2)}%</td>
        </tr>`;
      }).join('')}
      <tr class="border-t border-yellow-800">
        <td class="py-1 text-yellow-400">📊 0050 benchmark</td>
        <td class="text-right">—</td>
        <td class="text-right font-mono text-yellow-400">${benchRet>=0?'+':''}${benchRet.toFixed(2)}%</td>
        <td class="text-right text-gray-600">基準</td>
      </tr>
      </tbody>
    </table>`;
  } catch(e) {
    const el = document.getElementById('dc_v5_accounts');
    if (el) el.textContent = 'V5 帳戶載入失敗';
  }
}
"""

if "loadV5Summary" not in c:
    c = c.replace(
        "async function loadRestrictions() {",
        V5_SUMMARY_JS + "\nasync function loadRestrictions() {"
    )
    # 加入 DOMContentLoaded 呼叫
    c = c.replace(
        "  loadRestrictions();",
        "  loadRestrictions();\n  loadV5Summary();"
    )
    print("✓ loadV5Summary JS 加入")
else:
    print("- loadV5Summary 已存在")

with open("frontend/templates/v3_dashboard.html","w") as f:
    f.write(c)
print("✓ v3_dashboard.html 更新完成")
