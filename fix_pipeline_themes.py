path = 'scripts/daily_pipeline.py'
with open(path) as f:
    c = f.read()
if '7c_theme_trends' in c:
    print("✓ 已修，跳過"); raise SystemExit

anchor = '''    # ── 步驟 8：資料品質檢查 ──'''
insert = '''    # ── 步驟 7c：主題熱度 ──
    def _themes():
        from backend.services.latest_update import update_theme_trends
        r = update_theme_trends(target_date)
        return {"ok": True, "message": f"主題 {r.get('themes_updated','?')} 個"}
    step("7c_theme_trends", _themes)

    # ── 步驟 8：資料品質檢查 ──'''
if anchor in c:
    c = c.replace(anchor, insert, 1)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ 主題熱度已加入每日 pipeline（步驟7c）")
else:
    print("❌ 找不到錨點")
