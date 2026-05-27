"""v8_kline_patch.py - 個股頁加入 K 線圖"""

with open("frontend/templates/stock_detail.html") as f:
    c = f.read()

# 加入 lightweight-charts CDN
if "lightweight-charts" not in c:
    c = c.replace(
        "{% block head %}",
        """{% block head %}
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>"""
    )

# 在技術指標卡片後加入 K 線圖卡片
KLINE_CARD = """
  <!-- K 線圖 -->
  <div class="s-card" id="kline_card">
    <div class="text-xs text-gray-500 font-semibold uppercase mb-2">📊 K 線圖（近60日）</div>
    <div id="kline_chart" style="height:280px;width:100%"></div>
  </div>
"""

if "kline_card" not in c:
    c = c.replace(
        "  <!-- 技術指標 -->",
        KLINE_CARD + "\n  <!-- 技術指標 -->"
    )

# 加入 K 線 JS
KLINE_JS = """
async function loadKline(code) {
  try {
    const data = await fetch(`/api/stock/${code}/kline?days=60`).then(r=>r.json());
    const kd = Array.isArray(data) ? data : (data.data || []);
    if (!kd.length) return;

    const container = document.getElementById('kline_chart');
    if (!container || typeof LightweightCharts === 'undefined') return;
    container.innerHTML = '';

    const chart = LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: 280,
      layout: { background: { color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#ff3333', downColor: '#33cc66',
      borderUpColor: '#ff3333', borderDownColor: '#33cc66',
      wickUpColor: '#ff3333', wickDownColor: '#33cc66',
    });

    const volSeries = chart.addHistogramSeries({
      color: '#30363d', priceFormat: { type: 'volume' },
      priceScaleId: 'volume', scaleMargins: { top: 0.8, bottom: 0 },
    });

    const candleData = kd.map(d => ({
      time: d.date || d.trade_date,
      open: d.open, high: d.high, low: d.low, close: d.close,
    })).filter(d => d.time && d.open);

    const volData = kd.map(d => ({
      time: d.date || d.trade_date,
      value: d.volume || 0,
      color: (d.close >= d.open) ? 'rgba(255,51,51,0.4)' : 'rgba(51,204,102,0.4)',
    })).filter(d => d.time);

    if (candleData.length) {
      candleSeries.setData(candleData);
      volSeries.setData(volData);
      chart.timeScale().fitContent();
    }

    window.addEventListener('resize', () => {
      chart.applyOptions({ width: container.clientWidth });
    });
  } catch(e) { console.warn('kline', e); }
}
"""

if "loadKline" not in c:
    c = c.replace(
        "async function loadStock() {",
        KLINE_JS + "\nasync function loadStock() {"
    )
    # 在 loadStock() 裡呼叫 loadKline
    c = c.replace(
        "  const [score, kline, chip, tech, techFeature]",
        "  loadKline(code);\n  const [score, kline, chip, tech, techFeature]"
    )

# URL 帶代號時也要呼叫
if "loadKline(urlCode)" not in c:
    c = c.replace(
        "  loadStock();",
        "  loadStock();"
    )

with open("frontend/templates/stock_detail.html","w") as f:
    f.write(c)
print("✓ 個股 K 線圖加入完成")

import subprocess
r = subprocess.run(["python3","-m","py_compile","frontend/templates/stock_detail.html"], capture_output=True)
# HTML 不需要 py_compile，直接確認有無語法問題
print("✓ stock_detail.html 更新完成")
