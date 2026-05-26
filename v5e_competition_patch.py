"""v5e_competition_patch.py - 月度競賽加入風調排名 + Strategy Health"""
import re

with open("frontend/templates/competition.html") as f:
    c = f.read()

# 1. 加入風調排名和 Strategy Health tab 按鈕
c = c.replace(
    '    <button class="tab-btn" onclick="switchTab(\'pnl\',this)">💰 每日損益</button>',
    '''    <button class="tab-btn" onclick="switchTab('pnl',this)">💰 每日損益</button>
    <button class="tab-btn" onclick="switchTab('riskadjusted',this)">⚖️ 風調排名</button>
    <button class="tab-btn" onclick="switchTab('health',this)">🏥 策略健康</button>'''
)

# 2. 加入新 tab 內容
c = c.replace(
    "  <!-- Tab: 每日損益 -->\n  <div id=\"tab_pnl\" class=\"hidden\">",
    """  <!-- Tab: 每日損益 -->
  <div id="tab_pnl" class="hidden">"""
)

# 在 </div> 最後加入新 tab
c = c.replace(
    "  <!-- Tab: 每日損益 -->\n  <div id=\"tab_pnl\" class=\"hidden\">\n    <div class=\"race-card\">\n      <canvas id=\"pnl_chart\" style=\"max-height:350px\"></canvas>\n    </div>\n  </div>\n</div>",
    """  <!-- Tab: 每日損益 -->
  <div id="tab_pnl" class="hidden">
    <div class="race-card">
      <canvas id="pnl_chart" style="max-height:350px"></canvas>
    </div>
  </div>

  <!-- Tab: 風調排名 -->
  <div id="tab_riskadjusted" class="hidden">
    <div class="race-card mb-3">
      <div class="text-xs text-gray-500 mb-2">風調分數 = Alpha×45% + 風調報酬×25% + 勝率×10% + 回撤控制×20%</div>
      <div id="risk_adjusted_list">載入中...</div>
    </div>
  </div>

  <!-- Tab: Strategy Health -->
  <div id="tab_health" class="hidden">
    <div class="race-card">
      <div class="text-xs text-gray-500 mb-3">策略健康狀態：資料覆蓋率、交易品質、風險指標</div>
      <div id="strategy_health_list">載入中...</div>
    </div>
  </div>
</div>"""
)

# 3. 更新 switchTab
c = c.replace(
    "['race','chart','alpha','drawdown','pnl'].forEach(t => {",
    "['race','chart','alpha','drawdown','pnl','riskadjusted','health'].forEach(t => {"
)
c = c.replace(
    "  if (tab === 'drawdown') loadDrawdownChart();\n  if (tab === 'pnl') loadPnlChart();",
    "  if (tab === 'drawdown') loadDrawdownChart();\n  if (tab === 'pnl') loadPnlChart();\n  if (tab === 'riskadjusted') loadRiskAdjusted();\n  if (tab === 'health') loadStrategyHealth();"
)

# 4. 加入風調排名和策略健康 JS
EXTRA_JS = """
// ── 風調排名 ──
async function loadRiskAdjusted() {
  try {
    const today = new Date();
    const startDate = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-01`;
    const d = await fetch(`/api/monthly/risk-adjusted-ranking?start_date=${startDate}`).then(r=>r.json());
    const accts = d.accounts || [];
    document.getElementById('risk_adjusted_list').innerHTML = accts.length ?
      `<table class="w-full text-xs">
        <thead><tr class="text-gray-500 border-b border-gray-700">
          <th class="text-left py-2">排名</th><th>帳戶</th>
          <th class="text-right">風調分</th><th class="text-right">Alpha</th>
          <th class="text-right">勝率</th><th class="text-right">回撤</th>
          <th class="text-right">波動</th><th class="text-right">成交</th><th>警告</th>
        </tr></thead><tbody>
        ${accts.map((a,i)=>`<tr class="border-t border-gray-800">
          <td class="py-1">${i===0?'🥇':i===1?'🥈':i===2?'🥉':'#'+(i+1)}</td>
          <td>${a.account_name}</td>
          <td class="text-right font-bold" style="color:${a.risk_adjusted_score>=60?'#44ff88':'#ffcc00'}">${a.risk_adjusted_score}</td>
          <td class="text-right ${a.alpha_vs_0050>=0?'text-green-400':'text-red-400'}">${a.alpha_vs_0050>=0?'+':''}${a.alpha_vs_0050}%</td>
          <td class="text-right">${a.win_rate}%</td>
          <td class="text-right text-red-400">${a.max_drawdown}%</td>
          <td class="text-right text-gray-500">${a.volatility}%</td>
          <td class="text-right">${a.trade_count}</td>
          <td class="text-xs text-yellow-400">${(a.warnings||[]).join(' ')}</td>
        </tr>`).join('')}
        </tbody></table>` :
      '<div class="text-gray-600 text-xs py-4 text-center">尚無資料</div>';
  } catch(e) { document.getElementById('risk_adjusted_list').textContent='載入失敗'; }
}

// ── Strategy Health ──
async function loadStrategyHealth() {
  try {
    const [registry, race, freshness] = await Promise.all([
      fetch('/api/strategy_registry').then(r=>r.json()),
      fetch('/api/monthly/race').then(r=>r.json()),
      fetch('/api/freshness').then(r=>r.json()),
    ]);

    const raceMap = {};
    (race.accounts||[]).forEach(a => raceMap[a.account_id] = a);

    document.getElementById('strategy_health_list').innerHTML = registry.length ?
      registry.map(reg => {
        const r = raceMap[reg.account_id] || {};
        const health = r.alpha_vs_0050 > 0 ? '🟢 健康' :
                       r.monthly_return > 0 ? '🟡 一般' : '🔴 需關注';
        return `<div class="p-2 mb-2 rounded border border-gray-700 text-xs">
          <div class="flex justify-between mb-1">
            <b>A${reg.account_id} ${reg.strategy_name}</b>
            <span>${health}</span>
          </div>
          <div class="text-gray-400 mb-1">${reg.description||''}</div>
          <div class="flex gap-4">
            <span>報酬 <b class="${(r.monthly_return||0)>=0?'text-green-400':'text-red-400'}">${(r.monthly_return||0)>=0?'+':''}${r.monthly_return||0}%</b></span>
            <span>Alpha <b class="${(r.alpha_vs_0050||0)>=0?'text-green-400':'text-red-400'}">${(r.alpha_vs_0050||0)>=0?'+':''}${r.alpha_vs_0050||0}%</b></span>
            <span>勝率 <b>${r.win_rate||0}%</b></span>
            <span>成交 <b>${r.trade_count||0}筆</b></span>
          </div>
          ${(r.trade_count||0)<3?'<div class="text-yellow-400 mt-1">⚠️ 交易次數不足 3 筆，參考性有限</div>':''}
        </div>`;
      }).join('') :
      '<div class="text-gray-600 text-xs">尚無資料</div>';
  } catch(e) { document.getElementById('strategy_health_list').textContent='載入失敗'; }
}
"""

c = c.replace("document.addEventListener('DOMContentLoaded', loadRace);",
              EXTRA_JS + "\ndocument.addEventListener('DOMContentLoaded', loadRace);")

with open("frontend/templates/competition.html","w") as f:
    f.write(c)
print("✓ competition.html 加入風調排名 + Strategy Health")
