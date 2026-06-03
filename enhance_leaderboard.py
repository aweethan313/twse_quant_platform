"""
排行榜增強（精準鎖定版，idempotent）：
  1. 日期選擇器：月份 → 日期
  2. metrics API 加 monthly_return（最近 30 天報酬）
  3. 排行榜表格加「月報酬」欄位（表頭 + 資料格）
用法：python3 enhance_leaderboard.py
"""

# ── A. 後端 metrics API 加 monthly_return（只在 metrics 函數內）──
mp = 'main.py'
with open(mp) as f:
    mc = f.read()

idx = mc.find('def api_strategy_account_metrics')
end = mc.find('@app.get("/data-health', idx)
if idx < 0:
    print("❌ 找不到 metrics 函數")
else:
    seg = mc[idx:end]
    if 'monthly_return' in seg:
        print("✓ metrics 函數已有 monthly_return，跳過")
    else:
        # 1) 加計算（接在 alpha 行後）
        anchor = '        alpha = total_return - float(bench)'
        calc = anchor + '''

        # 月報酬：最近 30 天報酬（用 equity_curve）
        try:
            latest_sd = db.execute(_t(
                "SELECT MAX(snap_date) FROM equity_curve WHERE account_id=:id"
            ), {"id": account_id}).scalar()
            eq_30d = db.execute(_t("""
                SELECT total_equity FROM equity_curve
                WHERE account_id=:id AND snap_date <= date(:ls, '-30 days')
                ORDER BY snap_date DESC LIMIT 1
            """), {"id": account_id, "ls": latest_sd}).scalar() if latest_sd else None
            monthly_return = ((total_equity / float(eq_30d) - 1) * 100) if eq_30d and float(eq_30d) > 0 else total_return
        except Exception:
            monthly_return = total_return'''
        seg = seg.replace(anchor, calc, 1)
        # 2) 加進回傳 dict
        seg = seg.replace(
            '"total_return": round(total_return, 3),',
            '"total_return": round(total_return, 3),\n            "monthly_return": round(monthly_return, 3),',
            1
        )
        mc = mc[:idx] + seg + mc[end:]
        with open(mp, 'w') as f:
            f.write(mc)
        print("✓ metrics API 已加入 monthly_return")

# ── B. 前端 competition.html ──
cp = 'frontend/templates/competition.html'
with open(cp) as f:
    fc = f.read()

# B1. 月份 → 日期
if 'type="month" id="bench_start_input"' in fc:
    fc = fc.replace('type="month" id="bench_start_input"', 'type="date" id="bench_start_input"')
    fc = fc.replace('value="2025-01"', 'value="2026-05-25"')
    fc = fc.replace('>起始月份<', '>起始日期<')
    print("✓ 選擇器 → 日期粒度")
else:
    print("✓ 選擇器已是日期，略過")

# B2. rebuildBenchmark 吃完整日期
old_fn = '''async function rebuildBenchmark(yearMonth) {
  const input = document.getElementById('bench_start_input');
  if (input) { input.disabled = true; input.style.opacity='0.5'; }
  const startDate = yearMonth + '-01';
  globalStartDate = startDate;'''
if old_fn in fc:
    fc = fc.replace(old_fn, '''async function rebuildBenchmark(startDate) {
  const input = document.getElementById('bench_start_input');
  if (input) { input.disabled = true; input.style.opacity='0.5'; }
  globalStartDate = startDate;''')
    fc = fc.replace("const [y,m] = yearMonth.split('-');", "")
    fc = fc.replace('累積報酬（${y}/${parseInt(m)}/1 起）', '累積報酬（${startDate} 起）')
    print("✓ rebuildBenchmark 吃完整日期")
else:
    print("✓ rebuildBenchmark 已更新，略過")

# B3. 主排行榜表頭加月報酬（精準鎖定 3-th 那行）
if '<th>Alpha</th><th>總報酬</th><th>總資產</th>' in fc:
    fc = fc.replace('<th>Alpha</th><th>總報酬</th><th>總資產</th>',
                    '<th>Alpha</th><th>月報酬</th><th>總報酬</th><th>總資產</th>')
    print("✓ 表頭加入月報酬")
else:
    print("✓ 表頭已有月報酬，略過")

# B4. row 加月報酬資料格（在總報酬格前面）
ret_cell = '''        <td class="text-right font-mono ${ret>=0?'text-up':'text-dn'}">${ret>=0?'+':''}${parseFloat(ret).toFixed(2)}%</td>'''
if 'a.monthly_return' not in fc and ret_cell in fc:
    monthly_cell = '''        <td class="text-right font-mono ${(a.monthly_return??ret)>=0?'text-up':'text-dn'}">${(a.monthly_return??ret)>=0?'+':''}${parseFloat(a.monthly_return??ret).toFixed(2)}%</td>
''' + ret_cell
    fc = fc.replace(ret_cell, monthly_cell, 1)
    print("✓ row 加入月報酬資料格")
else:
    print("✓ row 已有月報酬或結構不同，略過")

with open(cp, 'w') as f:
    f.write(fc)
print("\n完成。重啟 server 後排行榜會多一欄「月報酬」，起始日選擇器變成可選日期。")
