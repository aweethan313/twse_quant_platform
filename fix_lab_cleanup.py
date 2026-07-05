import re

path = 'frontend/templates/lab.html'
with open(path) as f:
    c = f.read()

if 'LAB_CLEANUP_V1' in c:
    print("✓ 已清理過,跳過")
    raise SystemExit

removed, notfound = [], []

# ── A. href 唯一的卡片:用 href 刪 ──
by_href = [
    ('/v6/health',            '策略健康分數(併入排行榜)'),
    ('/v6/strategy-vs-0050',  '策略vs0050(併入排行榜)'),
    ('/v6/selection-heatmap', '選股熱圖(併入選股品質分析)'),
    ('/v7/sector-rotation',   '產業輪動(殭屍,6/1停更)'),
]
for href, label in by_href:
    pat = re.compile(r'\s*<a href="' + re.escape(href) + r'"[^>]*>.*?</a>', re.DOTALL)
    m = pat.search(c)
    if m:
        c = c[:m.start()] + c[m.end():]
        removed.append(label)
    else:
        notfound.append(label)

# ── B. href 共用(/v7、/v8):用卡片標題文字刪 ──
by_title = [
    ('美股事件影響', '美股事件(與市場總覽重疊)'),
    ('ML 評分',      'ML評分RF(已被lgbm取代)'),
    ('空頭壓力測試', '空頭壓測(一次性舊研究)'),
]
for title, label in by_title:
    pat = re.compile(r'\s*<a href="/v[78]"[^>]*>(?:(?!</a>).)*?' + re.escape(title) + r'(?:(?!</a>).)*?</a>', re.DOTALL)
    m = pat.search(c)
    if m:
        c = c[:m.start()] + c[m.end():]
        removed.append(label)
    else:
        notfound.append(label)

c += "\n<!-- LAB_CLEANUP_V1 -->\n"
with open(path, 'w') as f:
    f.write(c)

print(f"✓ 已移除 {len(removed)} 張:")
for r in removed:
    print(f"   - {r}")
if notfound:
    print(f"⚠️ 沒找到 {len(notfound)} 個(貼給 Claude):")
    for n in notfound:
        print(f"   - {n}")
