"""scripts/p0_fix_competition_chart.py
重寫月度競賽圖表為完整日期對齊版本
"""
import re, os

path = "frontend/templates/competition.html"
if not os.path.exists(path):
    print(f"❌ {path} 不存在")
    exit()

with open(path) as f:
    c = f.read()

# 找到 equity_curves 的處理區塊並替換成日期對齊版本
ALIGNED_CHART_JS = r"""
// ── P0-4 日期對齊圖表 ──
async function loadAlignedEquityChart() {
  try {
    var resp = await fetch('/api/competition/equity_curves');
    var raw = await resp.json();
    // raw 可能是 {curves: [...]} 或直接 [...]
    var accounts = Array.isArray(raw) ? raw : (raw.curves || raw.accounts || raw.data || []);
    if (!accounts.length) {
      console.warn('[competition] equity_curves 無資料');
      return;
    }

    // 1. 收集全部日期
    var dateSet = new Set();
    accounts.forEach(a => {
      var hist = a.equity_history || a.history || a.snapshots || [];
      if (!hist.length && Array.isArray(a)) hist = a;
      hist.forEach(e => {
        var d = e.date || e.snap_date || e.trade_date;
        if (d) dateSet.add(String(d).slice(0,10));
      });
    });
    var labels = Array.from(dateSet).sort();
    if (!labels.length) { console.warn('[competition] 無日期資料'); return; }

    var COLORS = ['#63b3ed','#48bb78','#f6ad55','#fc8181','#b794f4','#4fd1c7','#f687b3'];
    var datasets = accounts.map((a, i) => {
      var hist = a.equity_history || a.history || a.snapshots || [];
      if (!hist.length && Array.isArray(a)) hist = a;
      var map = {};
      hist.forEach(e => {
        var d = e.date || e.snap_date || e.trade_date;
        if (d) map[String(d).slice(0,10)] = +(e.total_equity || e.equity || e.value || 0);
      });
      // 基準值（第一個有資料的點）
      var baseVal = null;
      var data = labels.map(d => {
        var v = map[d];
        if (v == null || v === 0) return null;
        if (baseVal == null) baseVal = v;
        return baseVal > 0 ? +((v / baseVal - 1) * 100).toFixed(3) : 0;
      });
      return {
        label: a.name || a.strategy_name || ('S' + (a.account_id || a.id || i+1)),
        data,
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + '18',
        tension: 0.3, pointRadius: 1, borderWidth: 2, spanGaps: true,
      };
    });

    // 2. 找 canvas（多種可能 id）
    var canvasId = ['equityChart','equity_chart','competition_chart','chart_equity',
                    'monthlyChart','monthly_chart'].find(id => document.getElementById(id));
    if (!canvasId) {
      // 建立 canvas
      var div = document.querySelector('.chart-container, #chart_area, #equity_area, main');
      if (div) {
        var cvs = document.createElement('canvas');
        cvs.id = 'equityChart'; cvs.style.maxHeight='350px';
        div.appendChild(cvs);
        canvasId = 'equityChart';
      } else { console.warn('[competition] 找不到圖表容器'); return; }
    }

    var canvas = document.getElementById(canvasId);
    var existing = Chart.getChart ? Chart.getChart(canvas) : null;
    if (existing) existing.destroy();

    new Chart(canvas, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          title: {
            display: true,
            text: `策略報酬率比較（資料更新：${labels[labels.length-1]||'-'}）`,
            color: '#bbb', font: { size: 12 }
          },
          tooltip: {
            callbacks: {
              title: items => items[0]?.label || '',
              label: item => {
                if (item.raw == null) return item.dataset.label + ': 無資料';
                return item.dataset.label + ': ' + (item.raw >= 0 ? '+' : '') + item.raw.toFixed(2) + '%';
              }
            }
          },
          legend: { labels: { color: '#bbb', font: { size: 11 } } }
        },
        scales: {
          x: { ticks: { color: '#666', maxTicksLimit: 8 } },
          y: {
            ticks: {
              color: '#bbb',
              callback: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%'
            },
            grid: { color: '#21262d' }
          }
        }
      }
    });
    console.log('[competition] 圖表渲染完成', labels.length, '個日期', accounts.length, '個策略');
  } catch(e) {
    console.error('[competition] 圖表載入失敗', e);
  }
}
"""

if "loadAlignedEquityChart" not in c:
    # 在第一個 <script> 後插入
    c = c.replace("<script>", "<script>\n" + ALIGNED_CHART_JS, 1)
    print("✓ loadAlignedEquityChart 注入")
else:
    # 替換舊版
    old = re.search(r'// ── P0-4 日期對齊圖表 ──.*?^}', c, re.DOTALL | re.MULTILINE)
    if old:
        c = c[:old.start()] + ALIGNED_CHART_JS + c[old.end():]
        print("✓ loadAlignedEquityChart 更新")

# 確保 DOMContentLoaded 時呼叫
if "loadAlignedEquityChart" in c and "loadAlignedEquityChart()" not in c:
    if "DOMContentLoaded" in c:
        c = re.sub(
            r"document\.addEventListener\('DOMContentLoaded'[^;]+;",
            lambda m: m.group() + "\ndocument.addEventListener('DOMContentLoaded', loadAlignedEquityChart);",
            c, count=1
        )
    else:
        c += "\ndocument.addEventListener('DOMContentLoaded', loadAlignedEquityChart);\n"
    print("✓ DOMContentLoaded 呼叫加入")

with open(path, "w") as f:
    f.write(c)
print("✓ competition.html 完整更新")
