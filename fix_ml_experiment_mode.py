"""
ML 實驗模式:讓不同 horizon 的分數寫入獨立實驗表,與生產資料完全隔離

背景:
  ml_score_results 的唯一鍵是 (score_date, code),不含 model_version
  → 不同 horizon 的分數無法並存,後跑的會覆寫先跑的
  → 8/16 事故就是這樣發生的

修法:
  新建 ml_score_experiments 表,唯一鍵含 model_version
  給 ml_scorer 加 --experiment 參數:開啟時寫實驗表,不碰生產表

用法:
  python3 fix_ml_experiment_mode.py

之後的實驗指令:
  python3 twse_ml_eval/ml_scorer.py --db data/db/quant.db \\
      --mode full --horizon 60 --step 60 \\
      --experiment --model-version lgbm_exp_h60
"""
import sqlite3

# ── 1. 建實驗表 ──
con = sqlite3.connect('data/db/quant.db')
con.execute("""
CREATE TABLE IF NOT EXISTS ml_score_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    score_date TEXT NOT NULL,
    code TEXT NOT NULL,
    stock_name TEXT,
    model_version TEXT NOT NULL,
    horizon INTEGER,
    ml_score REAL,
    ml_rank INTEGER,
    predicted_return REAL,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(score_date, code, model_version)
)""")
con.execute("CREATE INDEX IF NOT EXISTS idx_mse_ver_date ON ml_score_experiments(model_version, score_date)")
con.commit()
con.close()
print("✓ ml_score_experiments 表已建立(唯一鍵含 model_version)")

# ── 2. patch ml_scorer ──
path = 'twse_ml_eval/ml_scorer.py'
with open(path) as f:
    src = f.read()

if 'EXPERIMENT_MODE' in src:
    print("✓ ml_scorer 已修,跳過")
    raise SystemExit

# 2a. 新增實驗寫入函式
helper = '''

def write_experiment_scores(db_path: str, scored, names: dict,
                            model_version: str, horizon: int):
    """EXPERIMENT_MODE:寫入獨立實驗表,不碰生產用的 ml_score_results"""
    con = sqlite3.connect(db_path)
    try:
        rows = [(r.date, r.code, names.get(r.code), model_version, horizon,
                 r.ml_score, int(r.ml_rank), r.predicted_return_5d)
                for r in scored.itertuples()]
        con.executemany("""
            INSERT INTO ml_score_experiments
                (score_date, code, stock_name, model_version, horizon,
                 ml_score, ml_rank, predicted_return)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(score_date, code, model_version) DO UPDATE SET
                stock_name=excluded.stock_name,
                ml_score=excluded.ml_score, ml_rank=excluded.ml_rank,
                predicted_return=excluded.predicted_return,
                created_at=datetime('now','localtime')
        """, rows)
        con.commit()
        return len(rows)
    finally:
        con.close()
'''

# 插在 write_scores 函式之後(找下一個 def)
idx = src.find('def write_scores(')
if idx < 0:
    print("❌ 找不到 write_scores")
    raise SystemExit
next_def = src.find('\ndef ', idx + 10)
if next_def < 0:
    print("❌ 找不到 write_scores 的結尾")
    raise SystemExit
src = src[:next_def] + helper + src[next_def:]

# 2b. 新增參數
old_arg = '''    ap.add_argument("--no-write", action="store_true",'''
new_arg = '''    ap.add_argument("--experiment", action="store_true",
                    help="實驗模式:寫入 ml_score_experiments,不碰生產表")
    ap.add_argument("--model-version", default=None,
                    help="實驗模式下的版本標記,如 lgbm_exp_h60")
    ap.add_argument("--no-write", action="store_true",'''
if old_arg in src:
    src = src.replace(old_arg, new_arg, 1)
else:
    print("❌ 找不到 --no-write 參數")
    raise SystemExit

# 2c. 放寬 horizon 保護:實驗模式也可通過
old_guard = '''    if args.horizon != _default_h and not args.no_write:'''
new_guard = '''    if args.experiment and not args.model_version:
        print("❌ --experiment 模式必須指定 --model-version(如 lgbm_exp_h60)")
        return
    if args.horizon != _default_h and not (args.no_write or args.experiment):'''
if old_guard in src:
    src = src.replace(old_guard, new_guard, 1)
else:
    print("❌ 找不到 horizon 保護")
    raise SystemExit

# 2d. 寫入分流
old_write = '''    if args.no_write:
        print(f"\\n⚠️ --no-write 模式:未寫入資料庫")'''
new_write = '''    if args.experiment:
        n = write_experiment_scores(args.db, scored, names,
                                    args.model_version, args.horizon)
        print(f"\\n✓ 已寫入 ml_score_experiments：{n:,} 筆")
        print(f"   model_version={args.model_version}, horizon={args.horizon} 日")
        print(f"   涵蓋 {len(sd)} 天（{sd[0]} ~ {sd[-1]}）")
        print(f"   (生產表 ml_score_results 未受影響)")
    elif args.no_write:
        print(f"\\n⚠️ --no-write 模式:未寫入資料庫")'''
if old_write in src:
    src = src.replace(old_write, new_write, 1)
else:
    print("❌ 找不到寫入分流錨點")
    raise SystemExit

with open(path, 'w') as f:
    f.write(src)

print("✓ ml_scorer 已加入 --experiment 模式")
print()
print("驗證:")
print("  grep -c 'EXPERIMENT_MODE' twse_ml_eval/ml_scorer.py")
print("  python3 -m py_compile twse_ml_eval/ml_scorer.py")
