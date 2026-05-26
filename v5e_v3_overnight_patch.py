"""v5e_v3_overnight_patch.py - V3 夜盤加入指數點數"""

with open("frontend/templates/v3_dashboard.html") as f:
    c = f.read()

# 修 loadMarket：改用增強版 API + 加入指數資訊
old_market = """// B. 大盤環境
async function loadMarket() {
  try {
    var d = await fetch('/api/market/overview').then(r=>r.json());
    var ctx = await fetch('/api/v3/strategies/router').then(r=>r.json());
    var ov = await fetch('/api/v2/overnight').then(r=>r.json());
    var trendMap = {bullish:'🟢 多頭',neutral:'🟡 盤整',bearish:'🔴 空頭'};
    var rl = ctx.risk_level || 'medium';
    var rlColor = rl==='high'?'#ff4444':rl==='low'?'#44ff88':'#ffcc00';
    document.getElementById('dc_market').innerHTML =
      row('市場趨勢', trendMap[ctx.market_trend]||ctx.market_trend||'—') +
      row('風險等級', `<span style="color:${rlColor}">${rl}</span>`) +
      row('部位倍率', `<span class="text-accent">${((ctx.position_multiplier||0.65)*100).toFixed(0)}%</span>`) +
      row('啟用策略', `<span class="text-green-400">${(ctx.enabled_strategies||[]).map(s=>'S'+s).join(' ')||'—'}</span>`) +
      row('美股夜盤', `<span class="text-blue-400">${ov.summary||'—'}</span>`) +
      row('上漲/下跌', `<span class="text-up">${d.up||0}</span> / <span class="text-dn">${d.down||0}</span>`);
  } catch(e) { document.getElementById('dc_market').textContent='載入失敗'; }
}"""

new_market = """// B. 大盤環境
async function loadMarket() {
  try {
    var [d, ctx, ov, enh] = await Promise.all([
      fetch('/api/market/overview').then(r=>r.json()),
      fetch('/api/v3/strategies/router').then(r=>r.json()),
      fetch('/api/v2/overnight').then(r=>r.json()),
      fetch('/api/v2/overnight-enhanced').then(r=>r.json()).catch(()=>({})),
    ]);
    var trendMap = {bullish:'🟢 多頭',neutral:'🟡 盤整',bearish:'🔴 空頭'};
    var rl = ctx.risk_level || 'medium';
    var rlColor = rl==='high'?'#ff4444':rl==='low'?'#44ff88':'#ffcc00';

    // 夜盤資訊
    var ovScore = enh.overnight_score || 50;
    var ovColor = ovScore >= 60 ? '#44ff88' : ovScore <= 40 ? '#ff4444' : '#ffcc00';
    var ovIcon = ovScore >= 60 ? '📈' : ovScore <= 40 ? '📉' : '➡️';

    // 台股代理（0050）
    var twse = enh.twse_proxy || {};
    var twseColor = (twse.change_pct||0) >= 0 ? '#ff6b6b' : '#51cf66';

    // 大盤廣度
    var breadth = enh.breadth || {};

    document.getElementById('dc_market').innerHTML =
      row('市場趨勢', trendMap[ctx.market_trend]||ctx.market_trend||'—') +
      row('風險等級', `<span style="color:${rlColor}">${rl}</span>`) +
      row('部位倍率', `<span class="text-accent">${((ctx.position_multiplier||0.65)*100).toFixed(0)}%</span>`) +
      row('0050', `<span class="font-mono font-bold">${twse.close||'—'}</span> <span style="color:${twseColor}">${(twse.change_pct||0)>=0?'+':''}${(twse.change_pct||0).toFixed(2)}%</span>`) +
      row('台股廣度', `<span class="text-up">↑${breadth.up||0}</span> <span class="text-dn">↓${breadth.down||0}</span> 均${(breadth.avg_change||0)>=0?'+':''}${(breadth.avg_change||0).toFixed(2)}%`) +
      row('夜盤情緒', `<span>${ovIcon}</span> <span style="color:${ovColor}">${enh.summary||ov.summary||'—'}</span>`) +
      row('成交金額', `<span class="text-gray-400">${breadth.total_value_b||0} 億</span>`);
  } catch(e) { document.getElementById('dc_market').textContent='載入失敗'; }
}"""

if old_market in c:
    c = c.replace(old_market, new_market)
    print("✓ V3 大盤環境加入夜盤增強資訊")
else:
    print("- V3 loadMarket 未找到，略過")

with open("frontend/templates/v3_dashboard.html","w") as f:
    f.write(c)
