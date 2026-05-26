"""v5d_competition_patch.py - 月度競賽加入回撤/損益/風調排名"""
import re

with open("frontend/templates/competition.html") as f:
    c = f.read()

# 1. 加入回撤和損益 tab 按鈕
old_tabs = """  <!-- Tabs -->
  <div class="flex gap-2 mb-4">
    <button class="tab-btn active" onclick="switchTab('race',this)">📊 Forward Paper Race</button>
    <button class="tab-btn" onclick="switchTab('chart',this)">📈 淨值曲線</button>
    <button class="tab-btn" onclick="switchTab('alpha',this)">🎯 Alpha vs 0050</button>
  </div>"""

new_tabs = """  <!-- Tabs -->
  <div class="flex gap-2 mb-4 flex-wrap">
    <button class="tab-btn active" onclick="switchTab('race',this)">📊 Forward Paper Race</button>
    <button class="tab-btn" onclick="switchTab('chart',this)">📈 淨值曲線</button>
    <button class="tab-btn" onclick="switchTab('alpha',this)">🎯 Alpha vs 0050</button>
    <button class="tab-btn" onclick="switchTab('drawdown',this)">📉 回撤曲線</button>
    <button class="tab-btn" onclick="switchTab('pnl',this)">💰 每日損益</button>
  </div>"""

c = c.replace(old_tabs, new_tabs)

# 2. 加入回撤和損益 tab 內容
old_alpha_div = """  <!-- Tab: Alpha -->
  <div id="tab_alpha" class="hidden">
    <div class="race-card">
      <canvas id="alpha_chart" style="max-height:350px"></canvas>
    </div>
  </div>
</div>"""

new_divs = """  <!-- Tab: Alpha -->
  <div id="tab_alpha" class="hidden">
    <div class="race-card">
      <canvas id="alpha_chart" style="max-height:350px"></canvas>
    </div>
  </div>

  <!-- Tab: 回撤曲線 -->
  <div id="tab_drawdown" class="hidden">
    <div class="race-card">
      <canvas id="drawdown_chart" style="max-height:350px"></canvas>
    </div>
  </div>

  <!-- Tab: 每日損益 -->
  <div id="tab_pnl" class="hidden">
    <div class="race-card">
      <canvas id="pnl_chart" style="max-height:350px"></canvas>
    </div>
  </div>
</div>"""

c = c.replace(old_alpha_div, new_divs)

# 3. 更新 switchTab 加入新 tab
c = c.replace(
    "['race','chart','alpha'].forEach(t => {",
    "['race','chart','alpha','drawdown','pnl'].forEach(t => {"
)
c = c.replace(
    "  if (tab === 'chart') loadEquityChart();\n  if (tab === 'alpha') loadAlphaChart();",
    "  if (tab === 'chart') loadEquityChart();\n  if (tab === 'alpha') loadAlphaChart();\n  if (tab === 'drawdown') loadDrawdownChart();\n  if (tab === 'pnl') loadPnlChart();"
)

# 4. 在月度競賽排行加入勝率/回撤
c = c.replace(
    "          <div class=\"text-xs text-gray-500 mt-0.5\">交易 ${a.trading_days||0} 天</div>",
    """          <div class="text-xs text-gray-500 mt-0.5 flex gap-3">
            <span>交易 ${a.trading_days||0} 天</span>
            <span>勝率 ${a.win_rate||0}%</span>
            <span class="text-red-400">回撤 ${a.max_drawdown||0}%</span>
            <span>成交 ${a.trade_count||0} 筆</span>
          </div>"""
)

# 5. 加入回撤和損益圖表 JS
EXTRA_CHARTS_JS = """
// ── 回撤曲線 ──
async function loadDrawdownChart() {
  if (window._ddChart) { window._ddChart.destroy(); window._ddChart = null; }
  try {
    const today = new Date();
    const startDate = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-01`;
    const data = await fetch(`/api/monthly/drawdown?start_date=${startDate}`).then(r=>r.json());
    if (!data.length) return;

    const allDates = new Set();
    data.forEach(a => (a.curve||[]).forEach(p => allDates.add(p.date)));
    const labels = [...allDates].sort();

    const datasets = data.map((a, i) => {
      const map = {};
      (a.curve||[]).forEach(p => map[p.date] = p.drawdown);
      return {
        label: a.name,
        data: labels.map(d => map[d] != null ? map[d] : null),
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + '15',
        fill: true,
        tension: 0.3, pointRadius: 1, spanGaps: true,
      };
    });

    window._ddChart = new Chart(document.getElementById('drawdown_chart'), {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          title: { display: true, text: '最大回撤曲線（負值=回撤）', color: '#888' },
          tooltip: { callbacks: {
            label: item => item.raw == null ? '' : `${item.dataset.label}: ${item.raw.toFixed(2)}%`
          }}
        },
        scales: {
          x: { ticks: { color: '#555', maxTicksLimit: 8 } },
          y: { ticks: { color: '#bbb', callback: v => v.toFixed(1)+'%' },
               grid: { color: '#21262d' } }
        }
      }
    });
  } catch(e) { console.error('drawdown chart', e); }
}

// ── 每日損益 Bar Chart ──
async function loadPnlChart() {
  if (window._pnlChart) { window._pnlChart.destroy(); window._pnlChart = null; }
  try {
    const today = new Date();
    const startDate = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-01`;
    const curves = await fetch(`/api/monthly/equity-curves?start_date=${startDate}`).then(r=>r.json());
    const accts = curves.filter(c => !c.is_benchmark);
    if (!accts.length) return;

    const allDates = new Set();
    accts.forEach(a => (a.curve||[]).forEach(p => allDates.add(p.date)));
    const labels = [...allDates].sort();

    const datasets = accts.map((a, i) => {
      const map = {};
      (a.curve||[]).forEach(p => map[p.date] = p.return_pct);
      const dates = [...allDates].sort();
      return {
        label: a.name,
        data: labels.map(d => map[d] != null ? +map[d] : null),
        backgroundColor: labels.map(d => (map[d]||0) >= 0 ?
          COLORS[i%COLORS.length]+'99' : COLORS[i%COLORS.length]+'44'),
        borderColor: COLORS[i%COLORS.length],
        borderWidth: 1,
      };
    });

    window._pnlChart = new Chart(document.getElementById('pnl_chart'), {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          title: { display: true, text: '每日累積報酬率', color: '#888' },
          tooltip: { callbacks: {
            label: item => item.raw == null ? '' :
              `${item.dataset.label}: ${item.raw>=0?'+':''}${item.raw.toFixed(2)}%`
          }}
        },
        scales: {
          x: { ticks: { color: '#555', maxTicksLimit: 10 } },
          y: { ticks: { color: '#bbb', callback: v => (v>=0?'+':'')+v.toFixed(1)+'%' },
               grid: { color: '#21262d' } }
        }
      }
    });
  } catch(e) { console.error('pnl chart', e); }
}
"""

c = c.replace("document.addEventListener('DOMContentLoaded', loadRace);",
              EXTRA_CHARTS_JS + "\ndocument.addEventListener('DOMContentLoaded', loadRace);")

with open("frontend/templates/competition.html","w") as f:
    f.write(c)
print("✓ competition.html 加入回撤/損益tab")
