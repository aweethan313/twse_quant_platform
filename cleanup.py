#!/usr/bin/env python3
"""cleanup.py — 安全清理腳本

預設只「預覽」要動什麼，不會真的改檔。確認無誤後加 --apply 才執行：
    python3 cleanup.py            # 預覽
    python3 cleanup.py --apply    # 真的清理

做三件事：
  1. 刪除誤建的 `{backend` 垃圾資料夾（大括號展開出錯的空目錄）
  2. 把 templates/ 的 .bak 與重複模板搬進 backup_broken_templates/
  3. 移除 main.py 中重複（被遮蔽）的第二個 /api/strategy-decisions 定義
     — 改寫前會自我驗證「語法正常 + 只剩一個定義」，不通過就跳過不動

把這支檔案放在專案根目錄（跟 main.py 同層）執行。
"""
import sys, re, ast, shutil
from pathlib import Path

APPLY = "--apply" in sys.argv
ROOT = Path(__file__).resolve().parent
tag = "[執行] " if APPLY else "[預覽] "


def section(t): print("\n" + "=" * 50 + f"\n  {t}\n" + "=" * 50)


# ── 1. 垃圾資料夾 ──
section("1. 垃圾資料夾 {backend")
junk = ROOT / "{backend"
if junk.exists():
    print(tag + f"刪除 {junk.name}/（及其所有空子目錄）")
    if APPLY:
        shutil.rmtree(junk)
else:
    print("✓ 沒有 {backend，無需處理")


# ── 2. 備份 / 重複模板 ──
section("2. templates/ 的備份與重複檔")
tdir = ROOT / "frontend" / "templates"
backup_dir = ROOT / "backup_broken_templates"
to_move = []
if tdir.exists():
    for f in sorted(tdir.iterdir()):
        name = f.name
        if (".bak" in name) or name in ("competition_backup.html", "competition_new.html"):
            to_move.append(f)
if to_move:
    for f in to_move:
        print(tag + f"搬移 {f.name} → backup_broken_templates/")
    if APPLY:
        backup_dir.mkdir(exist_ok=True)
        for f in to_move:
            shutil.move(str(f), str(backup_dir / f.name))
else:
    print("✓ templates/ 乾淨，無備份/重複檔")


# ── 3. main.py 重複路由 ──
section("3. main.py 重複的 /api/strategy-decisions")
main_py = ROOT / "main.py"
if not main_py.exists():
    print("⚠ 找不到 main.py（請確認腳本放在專案根目錄）")
else:
    src = main_py.read_text(encoding="utf-8")
    n = src.count('@app.get("/api/strategy-decisions")')
    if n < 2:
        print(f"✓ 只有 {n} 個定義，無需處理")
    else:
        # 第二個（要刪的）有獨特簽名
        marker = ('@app.get("/api/strategy-decisions")\n'
                  'def api_strategy_decisions(signal_date: str = None, '
                  'account_id: int = None, limit: int = 30):')
        idx = src.find(marker)
        if idx == -1:
            print("⚠ 找不到第二個定義的獨特簽名，請手動檢查 main.py 第 2710 行附近")
        else:
            rest = src[idx + len(marker):]
            m = re.search(r"\n(@app\.|# ──)", rest)  # 函式結束 = 下一個路由或區塊註解
            end = idx + len(marker) + (m.start() if m else len(rest))
            new_src = src[:idx].rstrip() + "\n\n" + src[end:].lstrip("\n")
            # 自我驗證
            try:
                ast.parse(new_src)
                syntax_ok = True
            except SyntaxError as e:
                syntax_ok = False
                err = e
            new_n = new_src.count('@app.get("/api/strategy-decisions")')
            if syntax_ok and new_n == 1:
                print(tag + "移除第二個（被遮蔽的）/api/strategy-decisions 定義")
                print("       （保留功能較完整的第一個）")
                if APPLY:
                    main_py.write_text(new_src, encoding="utf-8")
            else:
                print(f"⚠ 安全檢查未通過（語法OK={syntax_ok}、剩餘定義={new_n}），"
                      "不動 main.py，請手動處理")


print("\n" + "=" * 50)
if APPLY:
    print("✅ 清理完成。建議重啟 app 確認一切正常：uvicorn main:app --host 0.0.0.0 --port 8000")
else:
    print("以上為預覽。確認無誤後執行：python3 cleanup.py --apply")
print("💡 你有 git，動手前可先 git commit 現狀；萬一有問題用 git restore 即可復原。")
