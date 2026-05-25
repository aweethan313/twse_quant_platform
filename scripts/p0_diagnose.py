"""scripts/p0_diagnose.py - 診斷 P0 問題"""
import re, os

print("=== P0 診斷 ===\n")

# strategies.html
path = "frontend/templates/strategies.html"
if os.path.exists(path):
    with open(path) as f:
        c = f.read()
    print(f"strategies.html ({len(c)} bytes)")
    print(f"  loadAccounts 函式數: {len(re.findall(r'function loadAccounts', c))}")
    print(f"  setInterval(loadAccounts: {len(re.findall(r'setInterval\(loadAccounts', c))}次")
    print(f"  strategy_page_fix.js: {'有' if 'strategy_page_fix.js' in c else '無'}")
    print(f"  v3_router ID數: {c.count('id=\"v3_router\"')}")
    print(f"  v3_leaderboard ID數: {c.count('id=\"v3_leaderboard\"')}")
    # 找 v3_router 所在位置
    if 'id="v3_router"' in c:
        idx = c.find('id="v3_router"')
        print(f"  v3_router 位置: ...{c[idx-60:idx+60].replace(chr(10),' ')}...")

print()

# strategy_page_fix.js
path2 = "frontend/static/strategy_page_fix.js"
if os.path.exists(path2):
    with open(path2) as f:
        c2 = f.read()
    print(f"strategy_page_fix.js ({len(c2)} bytes)")
    print(f"  window.loadAccounts: {'有' if 'window.loadAccounts' in c2 else '無'}")
    print(f"  v3_router: {'有' if 'v3_router' in c2 else '無'}")
    print(f"  setInterval: {len(re.findall(r'setInterval', c2))}次")
    print(f"  T+1: {'有' if 'T+1' in c2 else '無'}")

print()

# competition.html
path3 = "frontend/templates/competition.html"
if os.path.exists(path3):
    with open(path3) as f:
        c3 = f.read()
    print(f"competition.html ({len(c3)} bytes)")
    print(f"  Chart.js 建立次數: {len(re.findall(r'new Chart\(', c3))}")
    print(f"  equity_curves API: {'有' if 'equity_curves' in c3 else '無'}")
    print(f"  alignEquityData: {'有' if 'alignEquityData' in c3 else '無'}")
    print(f"  buildAlignedChart: {'有' if 'buildAlignedChart' in c3 else '無'}")
    print(f"  loadAlignedEquityChart: {'有' if 'loadAlignedEquityChart' in c3 else '無'}")
    # chart canvas id
    canvas_ids = re.findall(r'id="([^"]*[Cc]hart[^"]*)"', c3)
    print(f"  chart canvas ids: {canvas_ids}")
