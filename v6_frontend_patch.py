"""v6_frontend_patch.py - 加入 V6 頁面路由 + 導覽列"""
import subprocess

# ── 1. 加入路由 ──
with open("main.py") as f:
    c = f.read()

V6_ROUTES = '''
# ── V6 頁面 ──
@app.get("/v6", response_class=HTMLResponse)
def page_v6(request: Request):
    return templates.TemplateResponse("v6_overview.html", {"request": request})

@app.get("/v6/backtest", response_class=HTMLResponse)
def page_v6_backtest(request: Request):
    return templates.TemplateResponse("v6_backtest.html", {"request": request})

@app.get("/v6/health", response_class=HTMLResponse)
def page_v6_health(request: Request):
    return templates.TemplateResponse("v6_health.html", {"request": request})

@app.get("/v6/candidate-quality", response_class=HTMLResponse)
def page_v6_candidate_quality(request: Request):
    return templates.TemplateResponse("v6_candidate_quality.html", {"request": request})
'''

if '"/v6"' not in c:
    c = c + V6_ROUTES
    print("✓ V6 路由加入")
else:
    print("- V6 路由已存在")

with open("main.py","w") as f:
    f.write(c)

# ── 2. 導覽列加入 V6 ──
with open("frontend/templates/base.html") as f:
    base = f.read()

if '"/v6"' not in base:
    base = base.replace(
        'href="/v3"',
        'href="/v6" class="nav-link {% block nav_v6 %}text-gray-400{% endblock %}">🔬 V6驗證</a>\n    <a href="/v3"'
    )
    with open("frontend/templates/base.html","w") as f:
        f.write(base)
    print("✓ 導覽列加入 V6")
else:
    print("- 導覽列已有 V6")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
