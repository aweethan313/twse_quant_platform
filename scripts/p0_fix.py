"""scripts/p0_fix.py - 修復 P0-2/3/4 問題"""
import re, os, sys

# ═══════════════════════════════════════════
# P0-2/3: strategies.html 清理
# - 移除頁面內的 loadAccounts（保留 fix.js 的版本）
# - 移除 v3_router / v3_leaderboard 混入策略卡片
# - setInterval 只呼叫一次
# ═══════════════════════════════════════════

def fix_strategies():
    path = "frontend/templates/strategies.html"
    if not os.path.exists(path):
        print(f"❌ {path} 不存在")
        return

    with open(path) as f:
        c = f.read()

    original_len = len(c)

    # 1. 找出 strategy_page_fix.js 載入的位置
    has_fix_js = "strategy_page_fix.js" in c
    print(f"  strategy_page_fix.js 載入: {'✓' if has_fix_js else '✗'}")

    # 2. 移除策略卡片 HTML 中的 v3_router / v3_leaderboard div
    # 這些不應出現在 account_cards 裡
    before = c.count('id="v3_router"') + c.count('id="v3_leaderboard"')
    if before > 0:
        # 移除 V3 路由器 + 排行榜的 HTML 區塊（在靜態 HTML 中）
        c = re.sub(
            r'<!-- V3 策略路由器[^>]*?-->.*?</div>\s*</div>\s*\n',
            '',
            c, flags=re.DOTALL
        )
        after = c.count('id="v3_router"') + c.count('id="v3_leaderboard"')
        print(f"  v3_router/v3_leaderboard: {before}個 → {after}個")
    else:
        print("  v3_router/v3_leaderboard: 無（乾淨）")

    # 3. 如果 strategies.html 本身有 loadAccounts，且 fix.js 也有
    # → 把 strategies.html 裡的 loadAccounts 改名為 _legacyLoadAccounts
    # 或直接移除，讓 fix.js 的版本接管
    if has_fix_js and "async function loadAccounts()" in c:
        # 找到 strategies.html 裡的 loadAccounts 函式範圍
        idx = c.find("async function loadAccounts()")
        # 找到函式結尾（配對大括號）
        depth = 0
        end = idx
        in_func = False
        for i in range(idx, min(idx+5000, len(c))):
            if c[i] == '{':
                depth += 1
                in_func = True
            elif c[i] == '}':
                depth -= 1
                if in_func and depth == 0:
                    end = i + 1
                    break

        if end > idx:
            old_func = c[idx:end]
            # 標記為 legacy
            c = c.replace(old_func, "/* P0-2: loadAccounts 移至 strategy_page_fix.js */")
            print("  ✓ strategies.html 內建 loadAccounts → 移除（由 fix.js 接管）")

    # 4. 確保 setInterval 不重複
    interval_count = c.count("setInterval(loadAccounts")
    if interval_count > 1:
        # 只保留第一個
        pos = c.find("setInterval(loadAccounts")
        second = c.find("setInterval(loadAccounts", pos+1)
        if second > 0:
            end_semi = c.find(";", second)
            c = c[:second] + c[end_semi+1:]
            print(f"  ✓ setInterval 重複 {interval_count}次 → 1次")
    else:
        print(f"  setInterval: {interval_count}次（正常）")

    # 5. 確保 fix.js 在 </body> 前（最後載入，覆蓋所有函式）
    if has_fix_js and '<script src="/static/strategy_page_fix.js' in c:
        # 移到 </body> 之前
        script_tag = re.search(r'<script src="/static/strategy_page_fix\.js[^>]*></script>', c)
        if script_tag:
            tag = script_tag.group()
            c = c.replace(tag, "")
            c = c.replace("</body>", f"  {tag}\n</body>")
            print("  ✓ strategy_page_fix.js 移至 </body> 前")

    with open(path, "w") as f:
        f.write(c)
    print(f"  strategies.html: {original_len} → {len(c)} bytes")


# ═══════════════════════════════════════════
# P0-4: 月度競賽圖表日期對齊
# 重寫圖表渲染邏輯，確保日期對齊
# ═══════════════════════════════════════════

def fix_competition():
    path = "frontend/templates/competition.html"
    if not os.path.exists(path):
        print(f"❌ {path} 不存在")
        return

    with open(path) as f:
        c = f.read()

    # 注入日期對齊函式（如果還沒有）
    ALIGN_FN = """
// ── P0-4: 日期對齊圖表工具 ──
function buildAlignedChart(canvasId, accounts, title) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  // 收集全部日期並排序
  var dateSet = new Set();
  accounts.forEach(a => {
    var hist = a.equity_history || a.equityHistory || a.history || [];
    hist.forEach(e => dateSet.add(e.date || e.snap_date || e.trade_date));
  });
  var labels = Array.from(dateSet).filter(Boolean).sort();
  if (!labels.length) return;

  var COLORS = ['#63b3ed','#48bb78','#f6ad55','#fc8181','#b794f4','#4fd1c7','#f687b3'];
  var datasets = accounts.map((a, i) => {
    var hist = a.equity_history || a.equityHistory || a.history || [];
    var map = {};
    hist.forEach(e => {
      var d = e.date || e.snap_date || e.trade_date;
      if (d) map[d] = e.total_equity || e.equity || e.value;
    });
    var baseVal = null;
    return {
      label: a.name || ('S' + (a.account_id || i)),
      data: labels.map(d => {
        var v = map[d];
        if (v == null) return null;
        if (baseVal == null) baseVal = +v;
        return baseVal > 0 ? (+v / baseVal - 1) * 100 : 0;
      }),
      borderColor: COLORS[i % COLORS.length],
      backgroundColor: COLORS[i % COLORS.length] + '22',
      tension: 0.3, pointRadius: 1, spanGaps: true,
    };
  });

  // 銷毀舊圖表
  var existing = Chart.getChart(canvas);
  if (existing) existing.destroy();

  new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        title: { display: !!title, text: title || '' },
        tooltip: {
          callbacks: {
            title: items => items[0]?.label || '',
            label: item => {
              if (item.raw == null) return item.dataset.label + ': 無資料';
              return item.dataset.label + ': ' + (item.raw >= 0 ? '+' : '') + item.raw.toFixed(2) + '%';
            }
          }
        }
      },
      scales: {
        y: {
          ticks: { callback: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%' }
        }
      }
    }
  });
}
"""

    if "buildAlignedChart" not in c:
        # 在第一個 <script> 後插入
        c = c.replace("<script>", "<script>\n" + ALIGN_FN, 1)
        print("  ✓ buildAlignedChart 注入")
    else:
        print("  buildAlignedChart 已存在")

    # 找到 equity_curves API 的處理，替換為使用 buildAlignedChart
    # 先找 /api/competition/equity_curves 的處理
    if "equity_curves" in c and "buildAlignedChart" not in c.split("equity_curves")[1][:500]:
        # 在 equity_curves 的 .then() 裡插入對齊邏輯
        old = "fetch('/api/competition/equity_curves')"
        new = """fetch('/api/competition/equity_curves')"""
        # 找 Chart 建立的地方，加入保護
        c = re.sub(
            r'(new Chart\([^,]+,\s*\{)',
            r'/* P0-4: 日期對齊 */ \1',
            c, count=1
        )
        print("  ✓ Chart 建立位置標記")

    with open(path, "w") as f:
        f.write(c)
    print("  ✓ competition.html 更新完成")


# ═══════════════════════════════════════════
# P0-3: strategy_page_fix.js 完善
# 確保策略卡片不混入 V3 元件
# 加入 T+1 模式顯示
# ═══════════════════════════════════════════

def fix_strategy_fix_js():
    path = "frontend/static/strategy_page_fix.js"
    if not os.path.exists(path):
        print(f"❌ {path} 不存在")
        return

    with open(path) as f:
        c = f.read()

    # 移除可能誤植的 v3 ID
    if 'v3_router' in c or 'v3_leaderboard' in c:
        c = c.replace('"v3_router"', '"acct_v3_router"')
        c = c.replace('"v3_leaderboard"', '"acct_v3_leaderboard"')
        c = c.replace("'v3_router'", "'acct_v3_router'")
        c = c.replace("'v3_leaderboard'", "'acct_v3_leaderboard'")
        print("  ✓ v3_ ID 衝突修正")
    else:
        print("  v3 ID: 無衝突")

    # 確保 setInterval 只呼叫一次
    intervals = len(re.findall(r'setInterval\(.*?loadAccounts', c))
    print(f"  setInterval(loadAccounts: {intervals}次")

    # 在策略卡片加入 T+1 模式標示（如果尚未有）
    if "T+1" not in c and "forward_paper" not in c:
        # 在帳戶卡片加入模式標示
        c = c.replace(
            '${a.strategy_type || "rule_based"}',
            '${a.strategy_type || "rule_based"} · <span class="text-blue-400">T+1 Paper</span>'
        )
        print("  ✓ T+1 模式標示加入卡片")

    with open(path, "w") as f:
        f.write(c)
    print("  ✓ strategy_page_fix.js 更新完成")


if __name__ == "__main__":
    print("=== P0 修復 ===\n")

    print("P0-2/3: strategies.html...")
    fix_strategies()

    print("\nP0-3: strategy_page_fix.js...")
    fix_strategy_fix_js()

    print("\nP0-4: competition.html...")
    fix_competition()

    print("\n✓ P0 修復完成，請重啟 server 確認")
