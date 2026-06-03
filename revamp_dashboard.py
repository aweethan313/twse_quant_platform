"""
每日作戰室改造：
  1. 刪除「大盤環境」「今日交易限制」「最近成交」三個區塊
  2. 換成「ML 選股檢討」區塊（5 日前選股 → 今日表現）
  3. 修「策略帳戶今日狀況」：改用 metrics API（正確欄位 + 5/25 起算）
  4. 清理 DOMContentLoaded（移除已刪區塊的 loader、加 ML 檢討 loader）
idempotent。用法：python3 revamp_dashboard.py
"""
path = 'frontend/templates/v3_dashboard.html'
with open(path) as f:
    c = f.read()

if 'dc_ml_review_summary' in c:
    print("✓ 已改造過，跳過")
    raise SystemExit

# ── 1. 大盤環境 + 今日交易限制 grid → ML 檢討卡片 ──
old_grid = '''  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">

    <!-- 大盤環境 -->
    <div class="dc-card">
      <div class="text-xs text-gray-500 mb-3 font-semibold uppercase">📈 大盤環境</div>
      <div id="dc_market">載入中...</div>
    </div>

    <!-- 今日限制 -->
    <div class="dc-card">
      <div class="text-xs text-gray-500 mb-2 font-semibold uppercase">🚫 今日交易限制</div>
      <div id="dc_restrictions_content"></div>
      <div id="dc_restrictions" class="text-xs"></div>
    </div>

  </div>'''
new_card = '''  <!-- ML 選股檢討 -->
  <div class="dc-card">
    <div class="flex items-center justify-between mb-2">
      <div class="text-xs text-gray-500 font-semibold uppercase">📋 ML 選股檢討（5 日前選股 → 今日表現）</div>
      <a href="/ml-picks" class="text-xs text-blue-400 hover:underline">完整 ML 選股 →</a>
    </div>
    <div id="dc_ml_review_summary" class="text-sm text-gray-400 mb-2">載入中...</div>
    <div id="dc_ml_review_table"></div>
  </div>'''
if old_grid in c:
    c = c.replace(old_grid, new_card)
    print("✓ 大盤環境/今日交易 → ML 檢討卡片")
else:
    print("⚠ 找不到大盤環境 grid（可能結構已不同）")

# ── 2. 移除「最近成交」卡片 ──
old_paper = '''  <!-- 模擬成交狀態 -->
  <div class="dc-card mt-3" id="dc_paper_status_card">
    <div class="text-xs text-gray-500 mb-2 font-semibold uppercase">📋 最近成交</div>
    <div id="dc_paper_status">載入中...</div>
  </div>

'''
if old_paper in c:
    c = c.replace(old_paper, '')
    print("✓ 移除最近成交卡片")
else:
    print("⚠ 找不到最近成交卡片")

# ── 3. 修策略帳戶表格：改用 metrics API ──
old_accounts = '''async function loadAccounts() {
  try {
    var data = await fetch('/api/strategies').then(r=>r.json());'''
new_accounts = '''async function loadAccounts() {
  try {
    var accts = await fetch('/api/strategy-accounts').then(r=>r.json());
    var data = await Promise.all(accts.map(a =>
      fetch(`/api/strategy-accounts/${a.account_id||a.id}/metrics?start_date=2026-05-25`)
        .then(r=>r.ok?r.json():null).then(m => ({
          account_id: a.account_id||a.id, name: a.name,
          total_equity: m?.total_equity, cash: m?.cash, market_value: m?.market_value,
          daily_return: m?.monthly_return, total_return: m?.total_return,
          last_trade_date: m?.trade_count ? (m.trade_count+' 筆') : '—'
        })).catch(()=>({account_id:a.account_id||a.id, name:a.name}))
    ));'''
if old_accounts in c:
    c = c.replace(old_accounts, new_accounts)
    # 把「今日損益」表頭改成「月報酬」（因為我們填的是 monthly_return）
    c = c.replace('<th class="text-right">今日損益</th>', '<th class="text-right">月報酬</th>')
    print("✓ 策略帳戶表格改用 metrics API")
else:
    print("⚠ 找不到 loadAccounts")

# ── 4. ML 檢討 loader 函數 + DOMContentLoaded 清理 ──
ml_loader = '''
async function loadDashboardMLReview(){
  const sEl = document.getElementById('dc_ml_review_summary');
  const tEl = document.getElementById('dc_ml_review_table');
  if(!sEl) return;
  try{
    const d = await fetch('/api/ml-review?top_n=5&hold_days=5').then(r=>r.json());
    if(d.error || !d.details){ sEl.textContent = d.error || '尚無檢討資料'; return; }
    const aCol = d.alpha>=0?'text-up':'text-dn';
    sEl.innerHTML = `<span class="text-gray-300">${d.signal_date}→${d.exit_date}</span>　`+
      `命中率 <b class="${d.win_rate>=50?'text-up':'text-dn'}">${d.win_rate}%</b>　`+
      `平均 <b class="${d.avg_actual_return>=0?'text-up':'text-dn'}">${d.avg_actual_return>=0?'+':''}${d.avg_actual_return}%</b>　`+
      `vs 0050 Alpha <b class="${aCol}">${d.alpha>=0?'+':''}${d.alpha}%</b>`;
    tEl.innerHTML = `<table class="w-full text-xs"><thead><tr class="text-gray-500">
      <th class="text-left py-1">#</th><th class="text-left">代號</th><th class="text-left">名稱</th>
      <th class="text-right">預測</th><th class="text-right">實際</th></tr></thead><tbody>
      ${d.details.sort((a,b)=>a.rank-b.rank).map(r=>`<tr class="border-t border-gray-800">
        <td class="py-1 text-gray-500">${r.rank}</td>
        <td><a href="/stock/${r.code}" class="text-blue-400 font-mono">${r.code}</a></td>
        <td>${r.name||''}</td>
        <td class="text-right font-mono text-gray-400">${r.predicted_5d>=0?'+':''}${r.predicted_5d}%</td>
        <td class="text-right font-mono ${r.actual_ret>=0?'text-up':'text-dn'}">${r.actual_ret>=0?'+':''}${r.actual_ret}%</td>
      </tr>`).join('')}</tbody></table>`;
  }catch(e){ sEl.textContent = '載入失敗'; }
}
'''
# 插在 DOMContentLoaded 前
c = c.replace("document.addEventListener('DOMContentLoaded', () => {",
              ml_loader + "\ndocument.addEventListener('DOMContentLoaded', () => {")
# 移除已刪區塊的 loader 呼叫，加入 ML 檢討
c = c.replace("  loadMarket();\n", "  loadDashboardMLReview();\n")
c = c.replace("  loadRestrictions();\n", "")
c = c.replace("  loadPaperStatus();\n", "")
print("✓ DOMContentLoaded 已清理（移除 market/restrictions/paperStatus，加入 ML 檢討）")

with open(path, 'w') as f:
    f.write(c)
print("\n完成。重啟 server 後每日作戰室會用 ML 檢討取代那三個區塊，策略帳戶數字也會正確。")
