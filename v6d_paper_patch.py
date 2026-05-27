"""v6d_paper_patch.py - V6-5 Paper Trading 手動成交優先 UI 更新"""

with open("frontend/templates/paper.html") as f:
    c = f.read()

# 1. 今日決策表格加入成交來源標示
old_pending_table = """        `<table class="w-full text-xs">
        <thead><tr class="text-gray-500"><th class="text-left py-1">帳戶</th><th>代號</th><th class="text-right">分數</th><th class="text-right">參考價</th><th class="text-right">停損</th><th>理由</th></tr></thead>
        <tbody>
        ${pending.map(r=>`<tr class="border-t border-gray-800">
          <td class="py-1">A${r.account_id}</td>
          <td><span class="badge-buy">BUY</span> ${r.code}</td>
          <td class="text-right">${(r.final_score||0).toFixed(1)}</td>
          <td class="text-right font-mono">${r.reference_price||'—'}</td>
          <td class="text-right font-mono text-red-400">${r.stop_loss||'—'}</td>
          <td class="text-gray-500 text-xs">${(r.reason_summary||'').slice(0,25)}</td>
        </tr>`).join('')}
        </tbody>
      </table>`"""

new_pending_table = """        `<div class="text-xs text-yellow-400 mb-2 p-2 bg-yellow-900/20 rounded border border-yellow-800/30">
          📋 請手動輸入實際成交價。若不輸入，系統將於下次執行時用 T+1 開盤估算（標示為「估算成交」）。
        </div>
        <table class="w-full text-xs">
        <thead><tr class="text-gray-500"><th class="text-left py-1">帳戶</th><th>代號</th><th class="text-right">分數</th><th class="text-right">參考價</th><th class="text-right">停損</th><th>理由</th><th>操作</th></tr></thead>
        <tbody>
        ${pending.map(r=>`<tr class="border-t border-gray-800">
          <td class="py-1">A${r.account_id}</td>
          <td><span class="badge-buy">BUY</span> <span class="text-blue-400">${r.code}</span></td>
          <td class="text-right">${(r.final_score||0).toFixed(1)}</td>
          <td class="text-right font-mono">${r.reference_price||'—'}</td>
          <td class="text-right font-mono text-red-400">${r.stop_loss||'—'}</td>
          <td class="text-gray-500 text-xs">${(r.reason_summary||'').slice(0,20)}</td>
          <td><button onclick="quickFill(${r.account_id},'${r.code}','BUY',${r.suggested_shares||0},${r.reference_price||0})"
            class="text-xs px-2 py-0.5 rounded bg-green-900/40 text-green-400 border border-green-800">手動成交</button></td>
        </tr>`).join('')}
        </tbody>
      </table>`"""

if old_pending_table in c:
    c = c.replace(old_pending_table, new_pending_table)
    print("✓ 待成交表格加入手動成交按鈕")

# 2. 成交記錄加入成交來源標示
old_fills_table = """          <td><span class="${r.action==='BUY'?'badge-buy':'badge-sell'}">${r.action}</span></td>"""
new_fills_table = """          <td><span class="${r.action==='BUY'?'badge-buy':'badge-sell'}">${r.action}</span></td>"""

# 3. 加入成交記錄的 fill_source 欄位
old_fills_cols = """          <td class="text-right font-mono">${r.shares}</td>
          <td class="text-right font-mono">${r.fill_price}</td>
          <td class="text-right text-gray-500">${Math.round(r.fee||0)}</td>
          <td class="text-right font-mono ${r.action==='BUY'?'text-red-400':'text-green-400'}">${Math.round(r.net_amount||0).toLocaleString()}</td>"""

new_fills_cols = """          <td class="text-right font-mono">${r.shares}</td>
          <td class="text-right font-mono">${r.fill_price}</td>
          <td class="text-right"><span class="text-xs px-1 rounded ${r.fill_source==='manual'?'bg-blue-900/40 text-blue-400':'bg-gray-800 text-gray-500'}">${r.fill_source==='manual'?'手動':'估算'}</span></td>
          <td class="text-right text-gray-500">${Math.round(r.fee||0)}</td>
          <td class="text-right font-mono ${r.action==='BUY'?'text-red-400':'text-green-400'}">${Math.round(r.net_amount||0).toLocaleString()}</td>"""

if old_fills_cols in c:
    c = c.replace(old_fills_cols, new_fills_cols)
    print("✓ 成交記錄加入成交來源標示")

# 4. 加入成交來源統計
old_fills_header = """      <div class="text-xs text-gray-500 font-semibold uppercase tracking-wide">📜 最近成交記錄</div>"""
new_fills_header = """      <div class="text-xs text-gray-500 font-semibold uppercase tracking-wide">📜 最近成交記錄</div>
      <div id="fills_source_summary" class="text-xs text-gray-600 mt-1"></div>"""

c = c.replace(old_fills_header, new_fills_header)

# 5. 加入 quickFill 函式 和成交來源統計
QUICK_FILL_JS = """
// 快速手動成交
async function quickFill(aid, code, action, shares, refPrice) {
  const price = prompt(`手動輸入成交價（參考：${refPrice}）：`, refPrice);
  if (!price || isNaN(parseFloat(price))) return;
  const note = `手動成交 signal_date=${new Date().toISOString().slice(0,10)}`;
  const r = await fetch('/api/paper/manual-fill-v6', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      account_id: aid, code, action,
      shares: shares||1, fill_price: parseFloat(price), note,
      fill_date: new Date().toISOString().slice(0,10),
    })
  }).then(r=>r.json());
  if (r.ok) {
    alert(`✅ 手動成交\n${action} ${code} ${r.shares}股 @${r.fill_price}\n費用：${Math.round(r.fee)}`);
    loadFills(); loadPositions(); loadPending();
  } else {
    alert('❌ ' + (r.error || '成交失敗'));
  }
}

// 成交來源統計
async function loadFillsSourceSummary() {
  try {
    const d = await fetch('/api/paper/fills?limit=100').then(r=>r.json());
    const manual = d.filter(r=>r.fill_source==='manual').length;
    const simulated = d.filter(r=>r.fill_source!=='manual').length;
    const el = document.getElementById('fills_source_summary');
    if (el) el.innerHTML = `<span class="text-blue-400">手動成交 ${manual} 筆</span> | <span class="text-gray-500">估算成交 ${simulated} 筆</span>`;
  } catch(e) {}
}
"""

if "quickFill" not in c:
    c = c.replace("document.addEventListener('DOMContentLoaded', () => {",
                  QUICK_FILL_JS + "\ndocument.addEventListener('DOMContentLoaded', () => {")
    c = c.replace("loadAccounts().then(() => { loadPending(); loadFills(); });",
                  "loadAccounts().then(() => { loadPending(); loadFills(); loadFillsSourceSummary(); });")
    print("✓ quickFill JS 加入")

with open("frontend/templates/paper.html","w") as f:
    f.write(c)
print("✓ paper.html 更新完成")
