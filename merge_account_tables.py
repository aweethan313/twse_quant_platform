"""
合併每日作戰室「策略帳戶今日狀況」的兩張重複表格：
  - 把 Alpha 欄位 + 0050 benchmark 列併進主表
  - 移除下方重複的 V5 摘要表（loadV5Summary）
  - 主表依 Alpha 由高到低排序（跟排行榜一致）
idempotent。用法：python3 merge_account_tables.py
"""
path = 'frontend/templates/v3_dashboard.html'
with open(path) as f:
    c = f.read()

if 'mergedAccounts' in c:
    print("✓ 已合併過，跳過")
    raise SystemExit

# 1. 整段替換 loadAccounts
import re
start = c.find('async function loadAccounts() {')
if start < 0:
    print("❌ 找不到 loadAccounts")
    raise SystemExit
# 找函數結尾（下一個 "\n}\n" 之後）
end = c.find('\n}\n', start) + 3

new_fn = '''async function loadAccounts() {  // mergedAccounts
  try {
    var accts = await fetch('/api/strategy-accounts').then(r=>r.json());
    var data = await Promise.all(accts.map(a =>
      fetch(`/api/strategy-accounts/${a.account_id||a.id}/metrics?start_date=2026-05-25`)
        .then(r=>r.ok?r.json():null).then(m => ({
          account_id: a.account_id||a.id, name: a.name,
          total_equity: m?.total_equity, cash: m?.cash, market_value: m?.market_value,
          monthly_return: m?.monthly_return, total_return: m?.total_return,
          alpha: m?.alpha_vs_0050,
          last_trade_date: m?.trade_count ? (m.trade_count+' 筆') : '—'
        })).catch(()=>({account_id:a.account_id||a.id, name:a.name}))
    ));
    // 依 Alpha 由高到低排序
    data.sort((x,y)=>(y.alpha??-999)-(x.alpha??-999));
    // 取 0050 benchmark 報酬
    let benchRet = 0;
    try{
      const b = await fetch('/api/benchmark/0050').then(r=>r.json());
      const bl = Array.isArray(b)?b.slice(-1)[0]:b;
      benchRet = parseFloat(bl?.cumulative_return ?? 0);
    }catch(e){}
    document.getElementById('dc_accounts').innerHTML = `
      <table class="w-full text-xs">
        <thead><tr class="text-gray-500">
          <th class="text-left py-2">帳戶</th><th class="text-right">總資產</th>
          <th class="text-right">現金</th><th class="text-right">市值</th>
          <th class="text-right">月報酬</th><th class="text-right">累積報酬</th>
          <th class="text-right">Alpha</th><th class="text-left pl-3">最近交易</th>
        </tr></thead>
        <tbody>
        ${data.map(a=>`<tr class="border-t border-gray-800">
          <td class="py-1"><b>S${a.account_id}</b> ${a.name}</td>
          <td class="text-right font-mono">${Math.round(a.total_equity||0).toLocaleString()}</td>
          <td class="text-right font-mono">${Math.round(a.cash||0).toLocaleString()}</td>
          <td class="text-right font-mono">${Math.round(a.market_value||0).toLocaleString()}</td>
          <td class="text-right font-mono ${(a.monthly_return||0)>=0?'text-up':'text-dn'}">${(a.monthly_return||0)>=0?'+':''}${(+(a.monthly_return||0)).toFixed(2)}%</td>
          <td class="text-right font-mono ${(a.total_return||0)>=0?'text-up':'text-dn'}">${(a.total_return||0)>=0?'+':''}${(+(a.total_return||0)).toFixed(2)}%</td>
          <td class="text-right font-mono ${(a.alpha||0)>=0?'text-up':'text-dn'}">${(a.alpha||0)>=0?'+':''}${(+(a.alpha||0)).toFixed(2)}%</td>
          <td class="text-gray-500 pl-3">${a.last_trade_date||'—'}</td>
        </tr>`).join('')}
        <tr class="border-t border-yellow-800">
          <td class="py-1 text-yellow-400">📊 0050 benchmark</td>
          <td colspan="4"></td>
          <td class="text-right font-mono text-yellow-400">${benchRet>=0?'+':''}${benchRet.toFixed(2)}%</td>
          <td class="text-right text-gray-600">基準</td>
          <td></td>
        </tr>
        </tbody>
      </table>`;
  } catch(e) { document.getElementById('dc_accounts').textContent='載入失敗'; }
}
'''
c = c[:start] + new_fn + c[end:]
print("✓ loadAccounts 已改為合併表（含 Alpha + benchmark 列 + 排序）")

# 2. 移除 loadV5Summary 呼叫（下方重複表格不再渲染）
if '  loadV5Summary();\n' in c:
    c = c.replace('  loadV5Summary();\n', '')
    print("✓ 移除 loadV5Summary 呼叫（下方重複表消失）")
else:
    print("⚠ 找不到 loadV5Summary() 呼叫")

with open(path, 'w') as f:
    f.write(c)
print("\n完成。重啟 server 後策略帳戶只剩一張表，含 Alpha 與 0050 基準列。")
