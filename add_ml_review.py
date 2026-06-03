"""
把 ML 選股檢討報告接上系統：
  1. main.py 加 /api/ml-review 端點
  2. scripts/daily_pipeline.py 加「ML 檢討」步驟（檢討 5 交易日前的選股）
前置：先把 ml_review.py 放到 backend/services/
idempotent，重複跑沒事。
用法：python3 add_ml_review.py
"""

# ── A. main.py 加 API 端點 ──
mp = 'main.py'
with open(mp) as f:
    mc = f.read()

if '/api/ml-review' in mc:
    print("✓ main.py 已有 /api/ml-review，跳過")
else:
    anchor = '@app.get("/api/daily-review-history")'
    endpoint = '''@app.get("/api/ml-review")
def api_ml_review(signal_date: str = None, top_n: int = 10, hold_days: int = 5):
    """ML 選股檢討報告：某 signal 日的 Top N 選股 → 之後 hold_days 實際表現"""
    from backend.services.ml_review import generate_ml_review
    from datetime import date as ddate, timedelta
    from pathlib import Path
    sig = ddate.fromisoformat(signal_date) if signal_date else ddate.today() - timedelta(days=7)
    # 先看檔案是否已存在
    path = Path(f"data/reports/ml_review_{sig}.md")
    r = generate_ml_review(sig, top_n=top_n, hold_days=hold_days)
    if not r:
        # 退而求其次：回傳已存在的報告檔
        if path.exists():
            return {"signal_date": str(sig), "report_path": str(path),
                    "content": path.read_text(encoding="utf-8")}
        return {"error": "無資料或評估期間不足", "signal_date": str(sig)}
    r["content"] = Path(r["report_path"]).read_text(encoding="utf-8") if Path(r["report_path"]).exists() else ""
    return r


'''
    mc = mc.replace(anchor, endpoint + anchor, 1)
    with open(mp, 'w') as f:
        f.write(mc)
    print("✓ main.py 已加入 /api/ml-review 端點")

# ── B. daily_pipeline.py 加 ML 檢討步驟 ──
pp = 'scripts/daily_pipeline.py'
with open(pp) as f:
    pc = f.read()

if 'ml_review' in pc:
    print("✓ daily_pipeline 已有 ml_review 步驟，跳過")
else:
    anchor = '    # ── 步驟 8：資料品質檢查 ──'
    new_step = '''    # ── 步驟 7b：ML 選股檢討（檢討 5 交易日前的選股）──
    def _ml_review():
        from backend.services.ml_review import generate_ml_review
        db = SessionLocal()
        try:
            past = db.execute(text("""
                SELECT trade_date FROM (
                    SELECT DISTINCT trade_date FROM ohlcv_daily
                    WHERE trade_date < :d AND code GLOB '[0-9][0-9][0-9][0-9]'
                    ORDER BY trade_date DESC LIMIT 5
                ) ORDER BY trade_date ASC LIMIT 1
            """), {"d": str(target_date)}).scalar()
        finally:
            db.close()
        if not past:
            return {"ok": False, "message": "無足夠歷史可檢討"}
        r = generate_ml_review(date.fromisoformat(past), top_n=10, hold_days=5)
        if r:
            return {"ok": True, "message": f"ML檢討 {past}: 命中率{r['win_rate']:.0f}% 實際{r['avg_actual_return']:+.1f}%"}
        return {"ok": False, "message": "ML檢討資料不足"}
    step("7b_ml_review", _ml_review)

'''
    pc = pc.replace(anchor, new_step + anchor, 1)
    with open(pp, 'w') as f:
        f.write(pc)
    print("✓ daily_pipeline 已加入 ML 檢討步驟")

print("\n完成。/api/ml-review?signal_date=2026-05-25 可查當天選股的後續表現。")
