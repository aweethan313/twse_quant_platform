"""
回測帳戶隔離(方案A)v2:用整數內嵌避開參數綁定的排版問題。
- 前向帳戶:11 ~ 99(預設,行為完全不變)
- 回測帳戶:100 以上
用法:python3 fix_account_isolation2.py
"""

def patch(path, pairs, guard):
    with open(path) as f:
        c = f.read()
    if guard in c:
        print(f"✓ {path} 已修,跳過")
        return True
    missing = [o[:50] for o, n in pairs if o not in c]
    if missing:
        print(f"❌ {path} 錨點失敗:")
        for m in missing:
            print(f"   找不到: {m}...")
        return False
    for o, n in pairs:
        c = c.replace(o, n, 1)
    with open(path, 'w') as f:
        f.write(c)
    print(f"✓ {path} 已修改")
    return True


# ── decision_engine ──
ok1 = patch(
    'backend/v5/decision_engine.py',
    [
        ("def generate_strategy_decisions(signal_date: date = None) -> dict:",
         "def generate_strategy_decisions(signal_date: date = None, account_min: int = 11, account_max: int = 99) -> dict:"),
        ("WHERE c.is_active=1 AND c.account_id >= 11",
         "WHERE c.is_active=1 AND c.account_id >= {int(account_min)} AND c.account_id <= {int(account_max)}"),
    ],
    'account_min'
)
# 把該 SQL 字串改成 f-string(找 configs 查詢的三引號開頭)
if ok1:
    with open('backend/v5/decision_engine.py') as f:
        c = f.read()
    old = '''        configs = db.execute(text("""
            SELECT c.*, a.name as account_name, a.initial_cash'''
    new = '''        configs = db.execute(text(f"""
            SELECT c.*, a.name as account_name, a.initial_cash'''
    if old in c and 'text(f"""\n            SELECT c.*' not in c:
        with open('backend/v5/decision_engine.py', 'w') as f:
            f.write(c.replace(old, new, 1))
        print("  ✓ configs 查詢改為 f-string")
    else:
        print("  ⚠️ configs 查詢的 f-string 轉換需確認")

# ── paper_engine ──
ok2 = patch(
    'backend/v5/paper_engine.py',
    [
        ("def update_v5_equity(snap_date: date = None) -> dict:",
         "def update_v5_equity(snap_date: date = None, account_min: int = 11, account_max: int = 99) -> dict:"),
        ('"SELECT id, cash, initial_cash FROM strategy_accounts WHERE id >= 11"',
         'f"SELECT id, cash, initial_cash FROM strategy_accounts WHERE id >= {int(account_min)} AND id <= {int(account_max)}"'),
    ],
    'account_min'
)

print("\n請執行:python3 -m py_compile backend/v5/decision_engine.py backend/v5/paper_engine.py")
