"""
補裝籌碼監控 v2:按行內容定位(不依賴精確空白),避免錨點因空格差異失敗。
用法:python3 fix_chip_monitor2.py
"""
path = 'backend/collectors/daily_eod.py'
with open(path) as f:
    lines = f.readlines()

if any('_register_pending_chip' in l for l in lines):
    print("✓ 已裝,跳過")
    raise SystemExit

# 找 _collect_chips 函式範圍
start = None
for i, l in enumerate(lines):
    if l.startswith('def _collect_chips'):
        start = i
        break
if start is None:
    print("❌ 找不到 _collect_chips")
    raise SystemExit

# 在函式範圍內,找兩個 "return"(緊接在 logger.warning 之後那兩個)
inserted = 0
i = start
end = len(lines)
for j in range(start + 1, len(lines)):
    if lines[j].startswith('def '):  # 下一個函式,結束
        end = j
        break

# 從後往前插入(避免行號位移):找 logger.warning(...法人...) 下一行的 return
new_lines = lines[:]
for j in range(end - 1, start, -1):
    stripped = new_lines[j].strip()
    if stripped == 'return' and 'logger.warning' in new_lines[j-1]:
        indent = new_lines[j][:len(new_lines[j]) - len(new_lines[j].lstrip())]
        if '法人資料無' in new_lines[j-1]:
            reason = 'chip_empty'
        else:
            reason = 'chip_parse_empty'
        new_lines.insert(j, f'{indent}_register_pending_chip(db, trade_date, "{reason}")\n')
        inserted += 1

if inserted < 2:
    print(f"❌ 只找到 {inserted} 個插入點(應為 2),請貼 _collect_chips 給 Claude")
    raise SystemExit

# 加 helper 到檔案末尾
helper = '''

def _register_pending_chip(db: Session, trade_date, reason: str):
    """籌碼漏抓時登記 pending_chip,供 process_pending_chip 自動補。"""
    from sqlalchemy import text as _t
    try:
        db.execute(_t("""CREATE TABLE IF NOT EXISTS pending_chip(
            trade_date TEXT PRIMARY KEY, reason TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)"""))
        db.execute(_t("INSERT OR IGNORE INTO pending_chip(trade_date, reason) VALUES(:d,:r)"),
                   {"d": str(trade_date), "r": reason})
        db.commit()
        logger.warning(f"[EOD] {trade_date} 已登記 pending_chip({reason})")
    except Exception as e:
        logger.warning(f"[EOD] pending_chip 登記失敗: {e}")
'''
new_lines.append(helper)

with open(path, 'w') as f:
    f.writelines(new_lines)

cnt = sum(l.count('pending_chip') for l in new_lines)
print(f"✓ 已插入 {inserted} 個登記點 + helper,pending_chip 共出現 {cnt} 次")
