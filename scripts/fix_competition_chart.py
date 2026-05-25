"""scripts/fix_competition_chart.py - 修復月度競賽圖表日期對齊"""
import re, os

COMP_FILE = "frontend/templates/competition.html"

if not os.path.exists(COMP_FILE):
    print("❌ competition.html 不存在")
    exit()

with open(COMP_FILE) as f:
    c = f.read()

# 注入日期對齊邏輯
DATE_ALIGN_JS = """
// 月度競賽圖表日期對齊工具函式
function alignEquityData(accounts) {
  // 1. 收集所有日期
  var allDates = new Set();
  accounts.forEach(a => {
    (a.equity_history || []).forEach(e => allDates.add(e.date || e.snap_date));
  });
  var labels = Array.from(allDates).sort();

  // 2. 每個帳戶建 date->equity map，缺值補 null
  var datasets = accounts.map((a, i) => {
    var map = {};
    (a.equity_history || []).forEach(e => {
      map[e.date || e.snap_date] = e.total_equity || e.equity;
    });
    var COLORS = ['#63b3ed','#48bb78','#f6ad55','#fc8181','#b794f4','#4fd1c7','#f687b3'];
    return {
      label: a.name || ('S' + a.account_id),
      data: labels.map(d => map[d] != null ? +map[d] : null),
      borderColor: COLORS[i % COLORS.length],
      backgroundColor: COLORS[i % COLORS.length] + '20',
      tension: 0.3,
      pointRadius: 2,
      spanGaps: true,
    };
  });
  return { labels, datasets };
}
"""

# 插入 JS 工具函式（在第一個 <script> 後）
if "alignEquityData" not in c:
    c = c.replace("<script>", "<script>\n" + DATE_ALIGN_JS, 1)
    print("✓ alignEquityData 注入")
else:
    print("- alignEquityData 已存在")

# 修正圖表 tooltip 顯示日期
TOOLTIP_FIX = """
          callbacks: {
            title: (items) => items[0]?.label || '',
            label: (item) => {
              var v = item.raw;
              if (v == null) return item.dataset.label + ': 無資料';
              var pct = item.dataIndex > 0 && item.dataset.data[0]
                ? ((v / item.dataset.data[0] - 1) * 100).toFixed(2)
                : '0.00';
              return item.dataset.label + ': ' + Math.round(v).toLocaleString() + ' (' + (pct>=0?'+':'') + pct + '%)';
            }
          }"""

if "callbacks:" not in c:
    c = c.replace("plugins: {", "plugins: {\n          tooltip: {" + TOOLTIP_FIX + "\n          },", 1)
    print("✓ tooltip 日期+報酬率 修正")

with open(COMP_FILE,"w") as f:
    f.write(c)
print("✓ competition.html 已更新")
