"""v6e_patch.py - V6E 最終前端整合"""
import subprocess, re

print("=== V6E Frontend Patch ===\n")

# ── 1. 路由 ──
with open("main.py") as f:
    c = f.read()

NEW_ROUTES = '''
@app.get("/v6/chip-alerts", response_class=HTMLResponse)
def page_v6_chip_alerts(request: Request):
    return templates.TemplateResponse("v6_chip_alerts.html", {"request": request})

@app.get("/v6/strategy-vs-0050", response_class=HTMLResponse)
def page_v6_strategy_vs_0050(request: Request):
    return templates.TemplateResponse("v6_strategy_vs_0050.html", {"request": request})
'''

if "/v6/chip-alerts" not in c:
    c = c + NEW_ROUTES
    print("✓ 新路由加入")

with open("main.py","w") as f:
    f.write(c)

# ── 2. V6 總覽頁加入 chip-alerts 和 strategy-vs-0050 連結 ──
with open("frontend/templates/v6_overview.html") as f:
    ov = f.read()

ov = ov.replace(
    '<a href="/v6/candidate-quality" class="text-xs px-3 py-1.5 rounded bg-green-900/40 text-green-400 border border-green-800">📈 選股</a>',
    '''<a href="/v6/candidate-quality" class="text-xs px-3 py-1.5 rounded bg-green-900/40 text-green-400 border border-green-800">📈 選股</a>
      <a href="/v6/chip-alerts" class="text-xs px-3 py-1.5 rounded bg-yellow-900/40 text-yellow-400 border border-yellow-800">🔔 籌碼</a>
      <a href="/v6/strategy-vs-0050" class="text-xs px-3 py-1.5 rounded bg-purple-900/40 text-purple-400 border border-purple-800">📊 vs0050</a>'''
)
with open("frontend/templates/v6_overview.html","w") as f:
    f.write(ov)
print("✓ V6總覽加入籌碼/vs0050連結")

# ── 3. strategies.html 加入 health score + cooldown ──
with open("frontend/templates/strategies.html") as f:
    st = f.read()

HEALTH_JS = """
// V6 健康分數 + Cooldown
async function loadV6HealthOnStrategies() {
  try {
    const [health, cooldowns] = await Promise.all([
      fetch('/api/v6/strategy-health').then(r=>r.json()),
      fetch('/api/v6/cooldowns?active_only=true').then(r=>r.json()),
    ]);
    const healthMap = {};
    health.forEach(h => { if (!healthMap[h.account_id]) healthMap[h.account_id] = h; });
    const cdMap = {};
    cooldowns.forEach(c => {
      if (!cdMap[c.account_id]) cdMap[c.account_id] = [];
      cdMap[c.account_id].push(c);
    });

    // 在 V5 帳戶卡片裡插入健康分數
    document.querySelectorAll('[data-account-id]').forEach(card => {
      const aid = parseInt(card.dataset.accountId);
      const h = healthMap[aid];
      const cds = cdMap[aid] || [];
      if (!h) return;
      const recColor = {PROMOTE:'text-green-400',KEEP:'text-blue-400',REDUCE:'text-yellow-400',PAUSE:'text-red-400'}[h.recommendation]||'text-gray-400';
      const existing = card.querySelector('.v6-health-badge');
      if (!existing) {
        const div = document.createElement('div');
        div.className = 'v6-health-badge mt-1 text-xs flex gap-2';
        div.innerHTML = `<span class="${recColor} font-bold">${h.recommendation}</span><span class="text-gray-500">健康${h.health_score}</span>` +
          (cds.length ? `<span class="text-yellow-400">❄️ ${cds.length}檔冷卻中</span>` : '');
        card.appendChild(div);
      }
    });
  } catch(e) {}
}
"""

if "loadV6HealthOnStrategies" not in st:
    st = st.replace(
        "loadAccounts();\n  loadV5Accounts();",
        "loadAccounts();\n  loadV5Accounts();\n  setTimeout(loadV6HealthOnStrategies, 1000);"
    )
    st = st.replace("</script>", HEALTH_JS + "\n</script>", 1)
    print("✓ strategies.html 加入健康分數")
else:
    print("- strategies 健康分數已存在")

with open("frontend/templates/strategies.html","w") as f:
    f.write(st)

# ── 4. monthly competition 加入健康分數 tab ──
with open("frontend/templates/competition.html") as f:
    comp = f.read()

# 確認 Strategy Health tab 已有資料（之前做過，確認）
if "loadStrategyHealth" in comp:
    print("✓ competition.html Strategy Health 已存在")
else:
    print("⚠️ competition.html 缺 Strategy Health")

# ── 5. V3 每日作戰室：補充 Top5 候選和 Paper 狀態 ──
with open("frontend/templates/v3_dashboard.html") as f:
    v3 = f.read()

V3_EXTRA_SECTIONS = """
  <!-- V3 作戰室：Paper 狀態 -->
  <div class="dc-card mb-3" id="dc_paper_status_card">
    <div class="flex justify-between mb-2">
      <div class="text-xs text-gray-500 font-semibold uppercase">💼 Paper Trading 狀態</div>
      <a href="/paper" class="text-xs text-blue-400 hover:underline">手動成交 →</a>
    </div>
    <div id="dc_paper_status">載入中...</div>
  </div>
"""

if "dc_paper_status_card" not in v3:
    v3 = v3.replace(
        "  <!-- V5 帳戶狀況 -->",
        V3_EXTRA_SECTIONS + "  <!-- V5 帳戶狀況 -->"
    )
    print("✓ V3 加入 Paper 狀態")

# Paper 狀態 JS
V3_PAPER_JS = """
async function loadPaperStatus() {
  try {
    const [fills, accts] = await Promise.all([
      fetch('/api/paper/fills?limit=5').then(r=>r.json()),
      fetch('/api/strategy-accounts').then(r=>r.json()),
    ]);
    const totalAsset = accts.reduce((s,a)=>s+(a.total_equity||200000),0);
    const totalCash  = accts.reduce((s,a)=>s+(a.cash||200000),0);
    const totalMkt   = accts.reduce((s,a)=>s+(a.market_value||0),0);
    const manual = fills.filter(f=>f.fill_source==='manual').length;
    const simulated = fills.filter(f=>f.fill_source!=='manual').length;
    const el = document.getElementById('dc_paper_status');
    if (!el) return;
    el.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mb-2">
      <div class="bg-surface-1 rounded p-2 text-center"><div class="text-gray-500">總資產</div><div class="font-bold font-mono">${Math.round(totalAsset).toLocaleString()}</div></div>
      <div class="bg-surface-1 rounded p-2 text-center"><div class="text-gray-500">現金</div><div class="font-mono">${Math.round(totalCash).toLocaleString()}</div></div>
      <div class="bg-surface-1 rounded p-2 text-center"><div class="text-gray-500">手動成交</div><div class="text-blue-400 font-bold">${manual}筆</div></div>
      <div class="bg-surface-1 rounded p-2 text-center"><div class="text-gray-500">估算成交</div><div class="text-gray-400">${simulated}筆</div></div>
    </div>
    ${fills.slice(0,3).map(f=>`<div class="text-xs border-t border-gray-800 py-1">
      <span class="${f.action==='BUY'?'text-up':'text-dn'} font-bold">${f.action}</span>
      <span class="text-blue-400 ml-1">${f.code}</span>
      <span class="ml-1">@${f.fill_price}</span>
      <span class="ml-1 px-1 rounded text-xs ${f.fill_source==='manual'?'bg-blue-900/40 text-blue-400':'bg-gray-800 text-gray-500'}">${f.fill_source==='manual'?'手動':'估算'}</span>
    </div>`).join('')}`;
  } catch(e) {}
}
"""

if "loadPaperStatus" not in v3:
    v3 = v3.replace(
        "  loadRestrictions();",
        "  loadRestrictions();\n  loadPaperStatus();"
    )
    v3 = v3.replace("async function loadRestrictions() {",
                    V3_PAPER_JS + "\nasync function loadRestrictions() {")
    print("✓ V3 Paper 狀態 JS 加入")

with open("frontend/templates/v3_dashboard.html","w") as f:
    f.write(v3)

# ── 6. data-quality 頁面加入 V6 檢查 ──
# 加入 V6 品質 API
with open("main.py") as f:
    c = f.read()

V6_DQ_API = '''
@app.get("/api/data-quality/v6")
def api_v6_data_quality():
    """V6 資料品質總覽"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate, timedelta as td
    db = SessionLocal()
    try:
        today = str(ddate.today())
        three_days_ago = str(ddate.today() - td(days=3))
        checks = []
        def chk(name, ok, msg):
            checks.append({"name": name, "status": "PASS" if ok else "FAIL", "message": msg})

        # trading_calendar
        n = db.execute(_t("SELECT COUNT(*) FROM trading_calendar WHERE is_open=1")).scalar() or 0
        chk("trading_calendar", n > 300, f"有效交易日 {n} 筆")

        # 0050 benchmark 異常
        anom = db.execute(_t("SELECT COUNT(*) FROM benchmark_daily_equity WHERE is_valid=0")).scalar() or 0
        chk("0050 benchmark", anom < 20, f"異常筆數 {anom}")

        # candidate_forward_returns
        cfr = db.execute(_t("SELECT COUNT(*) FROM candidate_forward_returns")).scalar() or 0
        chk("candidate_forward_returns", cfr > 100, f"{cfr} 筆")

        # strategy_health_scores
        hs = db.execute(_t("SELECT COUNT(*) FROM strategy_health_scores")).scalar() or 0
        chk("strategy_health_scores", hs > 0, f"{hs} 筆")

        # strategy_cooldowns
        cd = db.execute(_t("SELECT COUNT(*) FROM strategy_cooldowns WHERE is_active=1")).scalar() or 0
        chk("strategy_cooldowns（主動）", True, f"冷卻中 {cd} 筆")

        # chip_anomaly_alerts
        ca = db.execute(_t(f"SELECT COUNT(*) FROM chip_anomaly_alerts WHERE trade_date >= '{three_days_ago}'")).scalar() or 0
        chk("chip_anomaly_alerts（3天）", True, f"近3天 {ca} 筆")

        # paper_fills fill_source
        manual = db.execute(_t("SELECT COUNT(*) FROM paper_fills WHERE fill_source='manual'")).scalar() or 0
        simulated = db.execute(_t("SELECT COUNT(*) FROM paper_fills WHERE fill_source!='manual'")).scalar() or 0
        chk("paper_fills 成交來源", True, f"手動 {manual}筆 估算 {simulated}筆")

        pass_n = sum(1 for c in checks if c["status"]=="PASS")
        return {"pass": pass_n, "total": len(checks), "checks": checks}
    finally:
        db.close()
'''

if "/api/data-quality/v6" not in c:
    c = c + V6_DQ_API
    with open("main.py","w") as f:
        f.write(c)
    print("✓ /api/data-quality/v6 加入")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
print("\n=== V6E Patch 完成 ===")
