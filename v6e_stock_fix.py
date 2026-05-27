"""v6e_stock_fix.py - 修個股頁技術指標和評分顯示"""

with open("frontend/templates/stock_detail.html") as f:
    c = f.read()

# 修技術指標：API 回傳格式不同
old_tech = """    const [score, kline, chip, tech] = await Promise.all([
      fetch(`/api/stock/${code}/latest_score`).then(r=>r.json()),
      fetch(`/api/stock/${code}/kline?days=2`).then(r=>r.json()),
      fetch(`/api/stock/${code}/chip`).then(r=>r.json()),
      fetch(`/api/stock/${code}/technical?days=1`).then(r=>r.json()),
    ]);

    // 基本
    const kd = (kline.data||[]);
    const last = kd[kd.length-1]||{};
    document.getElementById('s_name').textContent = kline.name || code;
    document.getElementById('s_code').textContent = code;
    document.getElementById('s_class').textContent = score.stock_class || '—';
    document.getElementById('s_price').textContent = last.close || '—';
    const chg = last.change_pct;
    document.getElementById('s_change').innerHTML = chg != null ?
      `<span class="${chg>=0?'text-up':'text-dn'}">${chg>=0?'+':''}${parseFloat(chg).toFixed(2)}%</span>` : '—';

    // 技術指標
    const t = Array.isArray(tech) ? tech[0] : tech;"""

new_tech = """    const [score, kline, chip, tech, techFeature] = await Promise.all([
      fetch(`/api/stock/${code}/latest_score`).then(r=>r.json()).catch(()=>({})),
      fetch(`/api/stock/${code}/kline?days=3`).then(r=>r.json()).catch(()=>({})),
      fetch(`/api/stock/${code}/chip`).then(r=>r.json()).catch(()=>[]),
      fetch(`/api/stock/${code}/technical?days=1`).then(r=>r.json()).catch(()=>[]),
      fetch(`/api/stock-tech?code=${code}`).then(r=>r.json()).catch(()=>({})),
    ]);

    // 基本
    const kd = Array.isArray(kline) ? kline : (kline.data||[]);
    const last = kd[kd.length-1]||{};
    document.getElementById('s_name').textContent = kline.name || code;
    document.getElementById('s_code').textContent = code;
    document.getElementById('s_class').textContent = (score.stock_class||score.class_) || '—';
    document.getElementById('s_price').textContent = last.close || '—';
    const chg = last.change_pct;
    document.getElementById('s_change').innerHTML = chg != null ?
      `<span class="${chg>=0?'text-up':'text-dn'}">${chg>=0?'+':''}${parseFloat(chg).toFixed(2)}%</span>` : '—';

    // 技術指標（支援多種 API 格式）
    const techArr = Array.isArray(tech) ? tech : (tech.data||[tech]);
    const t = techArr[0] || techFeature || {};"""

if old_tech in c:
    c = c.replace(old_tech, new_tech)
    print("✓ 技術指標 API 格式修正")
else:
    print("- 未找到，略過")

# 修評分顯示：latest_score 欄位名稱
old_score = """    document.getElementById('s_score').innerHTML = score.composite_score != null ? `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        ${[['綜合', score.composite_score],['動能', score.momentum_score],
           ['籌碼', score.chip_score],['風險', score.risk_score]].map(([l,v])=>`"""

new_score = """    const finalScore = score.composite_score ?? score.final_score;
    document.getElementById('s_score').innerHTML = finalScore != null ? `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        ${[['綜合', finalScore],['動能', score.momentum_score??score.momentum],
           ['籌碼', score.chip_score??score.chip],['風險', score.risk_score??score.risk]].map(([l,v])=>`"""

if old_score in c:
    c = c.replace(old_score, new_score)
    c = c.replace("score.composite_score != null", "finalScore != null")
    print("✓ 評分欄位名稱修正")

with open("frontend/templates/stock_detail.html","w") as f:
    f.write(c)

# 加入 /api/stock-tech 簡易 API
with open("main.py") as f:
    mc = f.read()

STOCK_TECH_API = '''
@app.get("/api/stock-tech")
def api_stock_tech_simple(code: str):
    """個股最新技術指標（簡易版）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        row = db.execute(_t("""
            SELECT trade_date, rsi14, distance_ma20, return_5d, ma5, ma20, ma60,
                   macd, macd_signal, atr14
            FROM technical_daily_features
            WHERE code=:c ORDER BY trade_date DESC LIMIT 1
        """), {"c": code}).fetchone()
        if not row:
            return {}
        return {"trade_date":row[0],"rsi14":row[1],"distance_ma20":row[2],
                "return_5d":row[3],"ma5":row[4],"ma20":row[5],"ma60":row[6],
                "macd":row[7],"macd_signal":row[8],"atr14":row[9]}
    finally:
        db.close()
'''

if "/api/stock-tech" not in mc:
    mc = mc + STOCK_TECH_API
    with open("main.py","w") as f:
        f.write(mc)
    print("✓ /api/stock-tech 加入")

import subprocess
r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
