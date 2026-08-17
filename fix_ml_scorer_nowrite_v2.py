"""
工程債 #1:ml_scorer 加入 --no-write 與實驗性參數保護(完整版)

背景(2026-08-16 事故):
  用 --horizon 60 測試 label 效果時,--mode full 直接覆寫整個
  ml_score_results 表(29 萬筆),且 model_version 未變 ——
  污染在資料庫層面完全看不出來,A7 差點用實驗模型做決策。

三道保護:
  1. --no-write 參數:只評估、不寫入
  2. horizon 非預設值時,強制要求 --no-write(否則拒絕執行)
  3. write_scores 呼叫處攔截,改印評估摘要

用法:python3 fix_ml_scorer_nowrite_v2.py
"""
path = 'twse_ml_eval/ml_scorer.py'
with open(path) as f:
    src = f.read()

if 'NO_WRITE_GUARD' in src:
    print("✓ 已修,跳過")
    raise SystemExit

changed = []

# ── 1) 新增 --no-write 參數(插在 --model 之前)──
anchors = [
    '    ap.add_argument("--model", default="auto")',
    '    ap.add_argument("--min-train", type=int, default=40)',
    '    ap.add_argument("--score-days", type=int, default=1',
]
added = False
for a in anchors:
    if a in src:
        src = src.replace(a,
            '    ap.add_argument("--no-write", action="store_true",\n'
            '                    help="只評估不寫入資料庫(實驗性參數測試用)")\n' + a, 1)
        changed.append("--no-write 參數")
        added = True
        break
if not added:
    print("❌ 找不到參數插入點")
    raise SystemExit

# ── 2) horizon 非預設值時強制 no-write ──
old_guard = '    embargo = args.embargo if args.embargo is not None else args.horizon'
if old_guard in src:
    new_guard = '''    # NO_WRITE_GUARD:實驗性 horizon 不可寫入生產資料
    # (2026-08-16 事故:--horizon 60 覆寫 29 萬筆分數,model_version 未變,無從察覺)
    _default_h = getattr(config, "DEFAULT_HORIZON", 5)
    if args.horizon != _default_h and not args.no_write:
        print(f"❌ horizon={args.horizon} 非預設值({_default_h}),屬實驗性參數。")
        print(f"   實驗結果不可寫入生產資料表。請加 --no-write 重跑。")
        return

''' + old_guard
    src = src.replace(old_guard, new_guard, 1)
    changed.append("horizon 保護")
else:
    print("⚠️ 找不到 embargo 錨點,跳過 horizon 保護")

# ── 3) write_scores 呼叫處攔截 ──
old_write = '''    names = load_names(args.db)
    n = write_scores(args.db, scored, names, importance)
    sd = sorted(scored["date"].unique())
    print(f"\\n✓ 已寫入 ml_score_results：{n:,} 筆，涵蓋 {len(sd)} 天（{sd[0]} ~ {sd[-1]}）")'''

new_write = '''    names = load_names(args.db)
    sd = sorted(scored["date"].unique())
    if args.no_write:
        print(f"\\n⚠️ --no-write 模式:未寫入資料庫")
        print(f"   評估結果:{len(scored):,} 筆,涵蓋 {len(sd)} 天（{sd[0]} ~ {sd[-1]}）")
        print(f"   horizon={args.horizon} 日,model={args.model}")
    else:
        n = write_scores(args.db, scored, names, importance)
        print(f"\\n✓ 已寫入 ml_score_results：{n:,} 筆，涵蓋 {len(sd)} 天（{sd[0]} ~ {sd[-1]}）")'''

if old_write in src:
    src = src.replace(old_write, new_write, 1)
    changed.append("write_scores 攔截")
else:
    print("⚠️ write_scores 錨點失敗(可能因換行符差異),請貼 200-215 行給 Claude")

with open(path, 'w') as f:
    f.write(src)

print(f"✓ 已套用:{', '.join(changed)}")
print("\n請執行驗證:")
print("  grep -c 'NO_WRITE_GUARD\\|no_write' twse_ml_eval/ml_scorer.py")
print("  python3 -m py_compile twse_ml_eval/ml_scorer.py")
