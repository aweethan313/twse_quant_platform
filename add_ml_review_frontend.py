"""
在 ML 選股頁面（ml_picks.html）加入「ML 選股檢討」區塊。
顯示：某 signal 日 Top N 選股 → 之後 5 日實際表現（命中率/方向準確/實際vs預測/Alpha）。
idempotent。用法：python3 add_ml_review_frontend.py
"""
path = 'frontend/templates/ml_picks.html'
with open(path) as f:
    c = f.read()

if 'ml_review_card' in c:
    print("✓ 已有 ML 檢討區塊，跳過")
else:
    # 1. 在「當日 ML Top 20」卡片後面插入檢討卡片
    anchor = '''  <p class="text-[11px] text-gray-600">
    ⚠️ 訊號吃市場 regime'''
    review_card = '''  <div class="mp-card" id="ml_review_card">
    <div class="flex items-center justify-between mb-3">
      <div class="text-xs text-gray-500 font-semibold uppercase">📋 ML 選股檢討（選股 → 之後 5 日實際表現）</div>
      <div class="flex items-center gap-2">
        <input type="date" id="mr_date"
          style="background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;padding:2px 8px;font-size:12px;cursor:pointer"
          onchange="loadMLReview(this.value)">
        <span class="text-xs text-gray-600">選股日</span>
      </div>
    </div>
    <div id="mr_summary" class="text-sm text-gray-400 mb-2">載入中...</div>
    <div id="mr_table"></div>
  </div>

'''
    c = c.replace(anchor, review_card + anchor, 1)

    # 2. 加入 JS（在 loadMLPicks(); 之前插入函數，並在後面呼叫）
    js_anchor = 'loadMLPicks();'
    js = '''
async function loadMLReview(sigDate){
  const summary = document.getElementById('mr_summary');
  const tableEl = document.getElementById('mr_table');
  try{
    // 預設：若沒指定，用 7 天前（確保 5 日持有期跑完）
    let url = '/api/ml-review?top_n=10&hold_days=5';
    if(sigDate) url += '&signal_date=' + sigDate;
    const d = await fetch(url).then(r=>r.json());
    if(d.error || !d.details){
      summary.textContent = d.error || '無檢討資料';
      tableEl.innerHTML = '';
      return;
    }
    // 同步日期選擇器
    const inp = document.getElementById('mr_date');
    if(inp && d.signal_date) inp.value = d.signal_date;

    const alphaColor = d.alpha>=0 ? 'text-up' : 'text-dn';
    summary.innerHTML =
      `<span class="text-gray-300">${d.signal_date} → ${d.exit_date}（持有 ${d.hold_days} 日）</span>　`+
      `命中率 <b class="${d.win_rate>=50?'text-up':'text-dn'}">${d.win_rate}%</b>　`+
      `方向準確 <b>${d.direction_accuracy}%</b>　`+
      `平均實際 <b class="${d.avg_actual_return>=0?'text-up':'text-dn'}">${d.avg_actual_return>=0?'+':''}${d.avg_actual_return}%</b> `+
      `<span class="text-gray-500">(預測 ${d.avg_predicted_return>=0?'+':''}${d.avg_predicted_return}%)</span>　`+
      `vs 0050 ${d.benchmark_return>=0?'+':''}${d.benchmark_return}% → Alpha <b class="${alphaColor}">${d.alpha>=0?'+':''}${d.alpha}%</b>`;

    tableEl.innerHTML = `
      <table class="w-full text-sm">
        <thead><tr class="text-gray-500 text-xs border-b border-border">
          <th class="text-left py-1.5">#</th><th class="text-left">代號</th>
          <th class="text-left">名稱</th><th class="text-right">預測5日</th>
          <th class="text-right">進場</th><th class="text-right">出場</th>
          <th class="text-right">實際報酬</th><th class="text-center">結果</th>
        </tr></thead>
        <tbody>${d.details.sort((a,b)=>a.rank-b.rank).map(r=>`
          <tr class="border-b border-border/40 hover:bg-surface-3/40">
            <td class="py-1.5 text-gray-500">${r.rank}</td>
            <td><a href="/stock/${r.code}" class="text-blue-400 font-mono hover:underline">${r.code}</a></td>
            <td>${r.name||''}</td>
            <td class="text-right font-mono text-gray-400">${r.predicted_5d>=0?'+':''}${r.predicted_5d}%</td>
            <td class="text-right font-mono text-gray-500">${r.entry}</td>
            <td class="text-right font-mono text-gray-500">${r.exit}</td>
            <td class="text-right font-mono font-bold ${r.actual_ret>=0?'text-up':'text-dn'}">${r.actual_ret>=0?'+':''}${r.actual_ret}%</td>
            <td class="text-center">${r.hit?'🔴漲':'🟢跌'} ${r.dir_correct?'<span class="text-up">✓</span>':'<span class="text-dn">✗</span>'}</td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  }catch(e){
    summary.textContent = '載入失敗：' + e;
  }
}
loadMLReview();
loadMLPicks();'''
    c = c.replace(js_anchor, js, 1)

    with open(path, 'w') as f:
        f.write(c)
    print("✓ ml_picks.html 已加入 ML 檢討區塊")

print("\n重啟 server 後，ML 選股頁面下方會出現「ML 選股檢討」，可選日期看後續表現。")
