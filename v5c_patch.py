"""v5c_patch.py"""
import subprocess

with open("main.py") as f:
    c = f.read()

# ── 1. /paper 路由 ──
if '"/paper"' not in c:
    PAPER_ROUTE = '''
@app.get("/paper", response_class=HTMLResponse)
def page_paper(request: Request):
    return templates.TemplateResponse("paper.html", {"request": request})
'''
    c = c.replace('@app.get("/v3"', PAPER_ROUTE + '\n@app.get("/v3"')
    print("✓ /paper 路由加入")

# ── 2. Manual fill API ──
if "/api/paper/manual-fill" not in c:
    MANUAL_FILL_API = '''
@app.post("/api/paper/manual-fill")
def api_manual_fill(body: dict):
    """手動輸入成交"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    import math

    aid = body.get("account_id")
    code = body.get("code")
    action = body.get("action", "BUY")
    shares = int(body.get("shares", 0))
    fill_price = float(body.get("fill_price", 0))
    note = body.get("note", "manual")

    if not all([aid, code, shares, fill_price]):
        return {"ok": False, "error": "缺少必填欄位"}

    FEE_RATE = 0.001425 * 0.38
    TAX_RATE = 0.003
    MIN_FEE = 20

    db = SessionLocal()
    try:
        acct = db.execute(_t("SELECT cash FROM strategy_accounts WHERE id=:id"), {"id": aid}).fetchone()
        if not acct:
            return {"ok": False, "error": f"帳戶 {aid} 不存在"}
        cash = float(acct[0] or 200000)

        gross = fill_price * shares
        fee = max(MIN_FEE, round(gross * FEE_RATE, 0))
        tax = round(gross * TAX_RATE, 0) if action == "SELL" else 0

        today = str(ddate.today())

        if action == "BUY":
            total_cost = gross + fee
            if total_cost > cash:
                return {"ok": False, "error": f"現金不足（需 {total_cost:,.0f}，有 {cash:,.0f}）"}
            db.execute(_t("UPDATE strategy_accounts SET cash=cash-:c WHERE id=:id"),
                       {"c": total_cost, "id": aid})
            pos = db.execute(_t("SELECT id, lots, avg_cost FROM positions WHERE account_id=:id AND code=:c"),
                              {"id": aid, "c": code}).fetchone()
            if pos:
                new_lots = float(pos[1]) + shares
                new_cost = (float(pos[1])*float(pos[2]) + shares*fill_price) / new_lots
                db.execute(_t("UPDATE positions SET lots=:l, avg_cost=:cost WHERE id=:pid"),
                           {"l": new_lots, "cost": new_cost, "pid": pos[0]})
            else:
                db.execute(_t("""INSERT INTO positions (account_id,code,lots,avg_cost,opened_at)
                    VALUES (:id,:c,:l,:cost,datetime('now','localtime'))"""),
                    {"id": aid, "c": code, "l": shares, "cost": fill_price})
            net = total_cost

        else:  # SELL
            pos = db.execute(_t("SELECT id, lots FROM positions WHERE account_id=:id AND code=:c"),
                              {"id": aid, "c": code}).fetchone()
            if not pos or float(pos[1]) < shares:
                return {"ok": False, "error": f"持股不足（有 {float(pos[1]) if pos else 0} 股）"}
            net = gross - fee - tax
            db.execute(_t("UPDATE strategy_accounts SET cash=cash+:p WHERE id=:id"),
                       {"p": net, "id": aid})
            new_lots = float(pos[1]) - shares
            if new_lots <= 0:
                db.execute(_t("DELETE FROM positions WHERE id=:pid"), {"pid": pos[0]})
            else:
                db.execute(_t("UPDATE positions SET lots=:l WHERE id=:pid"),
                           {"l": new_lots, "pid": pos[0]})

        db.execute(_t("""INSERT INTO paper_fills
            (account_id,code,action,shares,fill_price,fill_time,fill_source,
             fee,tax,gross_amount,net_amount,note,execution_date,no_lookahead_pass)
            VALUES (:id,:c,:a,:s,:p,datetime('now','localtime'),'manual',
                    :fee,:tax,:gross,:net,:note,:ed,1)"""),
            {"id": aid, "c": code, "a": action, "s": shares, "p": fill_price,
             "fee": fee, "tax": tax, "gross": gross, "net": net, "note": note, "ed": today})

        db.commit()
        return {"ok": True, "action": action, "code": code, "shares": shares,
                "fill_price": fill_price, "fee": fee, "net": net}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
'''
    c = c + MANUAL_FILL_API
    print("✓ /api/paper/manual-fill 加入")

# ── 3. 修 /api/monthly/race 加入 V5 帳戶（含勝率/回撤）──
if "win_rate" not in c:
    c = c.replace(
        '"trading_days": r[5],',
        '''"trading_days": r[5],
                "win_rate": 0,
                "max_drawdown": 0,'''
    )
    print("✓ monthly/race 加入 win_rate/max_drawdown")

# ── 4. 導覽列加入 Paper 連結 ──
with open("frontend/templates/base.html") as f:
    base = f.read()

if '"/paper"' not in base and 'Paper' not in base:
    base = base.replace(
        'href="/candidates"',
        'href="/candidates"',
    )
    # 在 V3 總覽後加入
    base = base.replace(
        '<a href="/v3"',
        '<a href="/paper" class="nav-link {% block nav_paper %}text-gray-400{% endblock %}">📋 Paper</a>\n    <a href="/v3"'
    )
    with open("frontend/templates/base.html","w") as f:
        f.write(base)
    print("✓ 導覽列加入 Paper 連結")
else:
    print("- Paper 連結已存在")

with open("main.py","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ main.py 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
