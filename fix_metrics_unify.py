"""P1-6:metrics 單一來源整併(刪699死碼、788轉發3331、月報酬欄吃monthly/race)"""
import re

# ── 刀1+刀2:main.py ──
path = 'main.py'
with open(path) as f:
    lines = f.readlines()
src = ''.join(lines)

if 'P16_UNIFIED' in src:
    print("✓ main.py 已修,跳過")
else:
    # 定位 699 死函式:找第一個「無裝飾器的 async def api_strategy_metrics」
    starts = [i for i, l in enumerate(lines) if l.startswith('async def api_strategy_metrics')]
    if len(starts) < 2:
        print("❌ 找不到兩個同名函式,中止"); raise SystemExit
    dead_start = starts[0]
    # 確認它上一行不是裝飾器(保險)
    if lines[dead_start-1].strip().startswith('@app'):
        print("❌ 第一個函式其實有路由,中止"); raise SystemExit
    # 函式結尾 = 下一個頂格內容行
    dead_end = None
    for j in range(dead_start+1, len(lines)):
        l = lines[j]
        if l.strip() and not l.startswith((' ', '\t', ')')):
            dead_end = j
            break
    print(f"刀1:刪除死函式 第{dead_start+1}~{dead_end}行(共{dead_end-dead_start}行)")
    del lines[dead_start:dead_end]
    src = ''.join(lines)

    # 刀2:788(現在行號已位移)的函式改成薄轉發
    m = re.search(r'@app\.get\("/api/strategies/\{account_id\}/metrics"\)\nasync def api_strategy_metrics\(account_id: int\):', src)
    if not m:
        print("❌ 找不到 788 路由錨點,中止"); raise SystemExit
    fwd_start = m.start()
    # 函式結尾:從函式體開始找下一個頂格行
    body_start = src.index('\n', m.end()) + 1
    rest = src[body_start:]
    mm = re.search(r'\n(?=[^\s)])', rest)
    fwd_end = body_start + (mm.start() + 1 if mm else len(rest))
    new_fwd = '''@app.get("/api/strategies/{account_id}/metrics")
async def api_strategy_metrics(account_id: int):
    """P16_UNIFIED:舊 V2 端點,轉發至統一的 strategy-accounts metrics(單一邏輯來源)"""
    return api_strategy_account_metrics(account_id)

'''
    src = src[:fwd_start] + new_fwd + src[fwd_end:]
    with open(path, 'w') as f:
        f.write(src)
    print("刀2:788 已改為轉發 3331 統一邏輯")

# ── 刀3:competition.html 月報酬欄吃 /api/monthly/race ──
path2 = 'frontend/templates/competition.html'
with open(path2) as f:
    h = f.read()
if 'P16_RACE_OVERRIDE' in h:
    print("✓ competition.html 已修,跳過")
else:
    old = '''    const combined = accts.map((a,i) => ({...a, ...(metrics[i]||{})}));'''
    new = '''    // P16_RACE_OVERRIDE:月報酬欄改吃 /api/monthly/race 的正確本月數字
    let raceMap = {};
    try {
      const race = await fetch('/api/monthly/race').then(r=>r.ok?r.json():null);
      (race?.accounts||[]).forEach(r => { raceMap[r.account_id] = r.monthly_return; });
    } catch(e) { console.warn('monthly race fetch failed', e); }
    const combined = accts.map((a,i) => ({...a, ...(metrics[i]||{}),
      monthly_return: raceMap[a.account_id] !== undefined ? raceMap[a.account_id] : (metrics[i]||{}).monthly_return}));'''
    if old in h:
        with open(path2, 'w') as f:
            f.write(h.replace(old, new, 1))
        print("刀3:月報酬欄已改吃 monthly/race(本月真實報酬)")
    else:
        print("❌ competition.html 錨點失敗")
