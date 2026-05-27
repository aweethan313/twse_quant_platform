"""v6c_warroom_patch.py - 把 V3 改成每日作戰室"""

with open("frontend/templates/v3_dashboard.html") as f:
    old_content = f.read()

# 在 <h1> 或 title 附近加入作戰室標題
import re

# 找並替換頁面 title 區域
new_header = """
  <!-- 每日作戰室 Header -->
  <div class="flex items-center justify-between mb-4">
    <div>
      <h1 class="text-xl font-bold">⚔️ 每日作戰室</h1>
      <div class="text-xs text-gray-500" id="dc_warroom_date">載入中...</div>
    </div>
    <div class="flex gap-2 items-center">
      <div id="dc_trading_allowed" class="text-xs px-3 py-1 rounded-full border">—</div>
      <div id="dc_benchmark_warning" class="text-xs text-orange-400 hidden">⚠️ benchmark 有異常</div>
    </div>
  </div>

  <!-- 交易限制卡片 -->
  <div class="bg-surface-2 border border-border rounded-lg p-3 mb-4" id="dc_trade_restrictions">
    <div class="text-xs text-gray-500 font-semibold mb-2">📋 今日交易限制</div>
    <div id="dc_restrictions_content" class="text-xs text-gray-400">載入中...</div>
  </div>

"""

# 在 <div class="p-4 space-y-4"> 或類似容器後插入
if "每日作戰室" not in old_content:
    # 插入在第一個主要 div 後
    old_content = re.sub(
        r'({% block content %}\s*\n)',
        r'\1' + new_header,
        old_content, count=1
    )
    print("✓ 作戰室 header 加入")
else:
    print("- 作戰室 header 已存在")

# 加入作戰室 JS
WAR_ROOM_JS = """
// ── 作戰室初始化 ──
async function initWarRoom() {
  try {
    // 最新交易日
    const cal = await fetch('/api/v6/trading-calendar/latest').then(r=>r.json()).catch(()=>null);
    const latestDate = cal?.latest_trading_date || new Date().toISOString().slice(0,10);
    document.getElementById('dc_warroom_date').textContent =
      `最新交易日：${latestDate} | ${new Date().toLocaleString('zh-TW')}`;

    // Benchmark 狀態
    const bench = await fetch('/api/v6/benchmark/status').then(r=>r.json()).catch(()=>null);
    if (bench?.has_anomaly) {
      document.getElementById('dc_benchmark_warning').classList.remove('hidden');
    }

    // 大盤狀況 → 判斷今日是否可交易
    const mkt = await fetch('/api/market/regime').then(r=>r.json()).catch(()=>null);
    const risk = mkt?.risk_level || 'medium';
    const allowed_el = document.getElementById('dc_trading_allowed');
    if (risk === 'high') {
      allowed_el.textContent = '🛑 高風險，建議觀察';
      allowed_el.className = 'text-xs px-3 py-1 rounded-full border border-red-700 text-red-400 bg-red-900/20';
    } else if (risk === 'low') {
      allowed_el.textContent = '✅ 可積極買進';
      allowed_el.className = 'text-xs px-3 py-1 rounded-full border border-green-700 text-green-400 bg-green-900/20';
    } else {
      allowed_el.textContent = '⚡ 正常操作';
      allowed_el.className = 'text-xs px-3 py-1 rounded-full border border-yellow-700 text-yellow-400 bg-yellow-900/20';
    }

    // 交易限制
    const ks = await fetch('/api/v4/strategies/kill-switch').then(r=>r.json()).catch(()=>({}));
    const ksActive = (ks?.strategies||[]).filter(s=>s.status==='KILL_SWITCH');
    const pm = parseFloat(mkt?.position_multiplier||1);
    document.getElementById('dc_restrictions_content').innerHTML = `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
        <div>買進 <span class="${risk==='high'?'text-red-400':'text-green-400'} font-bold">${risk==='high'?'暫停':'允許'}</span></div>
        <div>部位乘數 <span class="text-accent font-bold">${(pm*100).toFixed(0)}%</span></div>
        <div>禁止當沖 <span class="text-yellow-400 font-bold">✓ 系統強制</span></div>
        <div>Kill Switch <span class="${ksActive.length?'text-red-400':'text-gray-500'} font-bold">${ksActive.length ? ksActive.length+'個' : '無'}</span></div>
      </div>
      <div class="mt-1 text-gray-600">⚠️ 禁止買進後同日賣出（系統自動攔截）</div>
    `;
  } catch(e) { console.warn('warroom init', e); }
}
"""

if "initWarRoom" not in old_content:
    old_content = old_content.replace(
        "document.addEventListener('DOMContentLoaded', () => {",
        WAR_ROOM_JS + "\ndocument.addEventListener('DOMContentLoaded', () => {\n  initWarRoom();"
    )
    print("✓ 作戰室 JS 加入")

with open("frontend/templates/v3_dashboard.html","w") as f:
    f.write(old_content)
print("✓ v3_dashboard.html 更新完成")
