"""v5d_v3_decisions_patch.py - V3 改顯示 V5 決策"""

with open("frontend/templates/v3_dashboard.html") as f:
    c = f.read()

# 修 T+1 摘要區塊：顯示 V5 決策
old_summary = """  async function loadSignals() {
  try {
    var data = await fetch('/api/candidates/trade-plans?limit=10').then(r=>r.json());
    _allSignals = Array.isArray(data) ? data : [];
    var el = document.getElementById('dc_signals_summary');
    if (!el) return;
    var buys = _allSignals.filter(s=>!s.invalid_buy_condition);
    el.innerHTML = buys.slice(0,3).map(s=>
      `<span class="inline-block mr-3">✅ <b class="text-white">${s.name}</b> ${s.entry_price_low??'—'}~${s.entry_price_high??'—'}</span>`
    ).join('') + (buys.length > 3 ? `<span class="text-blue-400">...共${buys.length}檔</span>` : '');
  } catch(e) {
    var el = document.getElementById('dc_signals_summary');
    if (el) el.textContent='尚無建議';
  }
}"""

new_summary = """  async function loadSignals() {
  try {
    // 今日候選（供摘要用）
    var plans = await fetch('/api/candidates/trade-plans?limit=10').then(r=>r.json());
    _allSignals = Array.isArray(plans) ? plans : [];

    // V5 決策摘要
    const today = new Date().toISOString().slice(0,10);
    var v5dec = await fetch('/api/strategy-decisions?signal_date='+today+'&limit=30').then(r=>r.json());
    var v5buys = (Array.isArray(v5dec)?v5dec:[]).filter(d=>d.action==='BUY'&&!d.is_blocked);

    var el = document.getElementById('dc_signals_summary');
    if (!el) return;

    if (v5buys.length) {
      // 按帳戶分組
      var byAcct = {};
      v5buys.forEach(d => {
        if (!byAcct[d.account_id]) byAcct[d.account_id] = [];
        byAcct[d.account_id].push(d.code);
      });
      el.innerHTML = Object.entries(byAcct).map(([aid,codes])=>
        `<span class="inline-block mr-3 text-xs"><b class="text-accent">A${aid}</b>: ${codes.slice(0,3).join(' ')}${codes.length>3?'...':''}</span>`
      ).join('') + `<span class="text-gray-500 text-xs ml-2">共${v5buys.length}筆決策</span>`;
    } else {
      var buys = _allSignals.filter(s=>!s.invalid_buy_condition);
      el.innerHTML = buys.slice(0,3).map(s=>
        `<span class="inline-block mr-3">✅ <b class="text-white">${s.name}</b></span>`
      ).join('') + (buys.length > 3 ? `<span class="text-blue-400">...共${buys.length}檔</span>` : '');
    }
  } catch(e) {
    var el = document.getElementById('dc_signals_summary');
    if (el) el.textContent='尚無建議';
  }
}"""

if old_summary in c:
    c = c.replace(old_summary, new_summary)
    print("✓ V3 T+1 摘要改顯示 V5 決策")
else:
    print("- V3 loadSignals 已是新版")

with open("frontend/templates/v3_dashboard.html","w") as f:
    f.write(c)
print("✓ v3_dashboard.html 更新")
