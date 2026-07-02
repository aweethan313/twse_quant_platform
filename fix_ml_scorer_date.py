# ── 1. ml_scorer.py 加 --date ──
path = 'twse_ml_eval/ml_scorer.py'
with open(path) as f:
    c = f.read()

if '--date' in c:
    print("✓ ml_scorer 已修，跳過")
else:
    ok = True
    old1 = '''    ap.add_argument("--score-days", type=int, default=1, help="latest 模式：評最新幾天")'''
    new1 = '''    ap.add_argument("--score-days", type=int, default=1, help="latest 模式：評最新幾天")
    ap.add_argument("--date", default=None, help="指定評分日 YYYY-MM-DD；資料截至該日，防回填偷看未來")'''
    if old1 in c:
        c = c.replace(old1, new1, 1)
    else:
        ok = False; print("❌ 錨點1失敗")

    old2 = '''    embargo = args.embargo if args.embargo is not None else args.horizon'''
    new2 = '''    if getattr(args, "date", None):
        args.end = args.date  # 截斷資料至指定日：評分日正確 + 訓練不含未來
    embargo = args.embargo if args.embargo is not None else args.horizon'''
    if old2 in c:
        c = c.replace(old2, new2, 1)
    else:
        ok = False; print("❌ 錨點2失敗")

    old3 = '''        print(f"  模式：latest（評最新 {args.score_days} 天）")'''
    new3 = '''        if getattr(args, "date", None) and dates and str(dates[-1])[:10] != str(args.date)[:10]:
            print(f"  ⚠️ 指定日 {args.date} 無資料（實際最新 {dates[-1]}），中止不評分")
            return
        print(f"  模式：latest（評最新 {args.score_days} 天）")'''
    if old3 in c:
        c = c.replace(old3, new3, 1)
    else:
        ok = False; print("❌ 錨點3失敗")

    if ok:
        with open(path, 'w') as f:
            f.write(c)
        print("✓ ml_scorer.py 已加 --date（含截斷防未來 + 無資料保護）")

# ── 2. daily_pipeline.py 帶日期呼叫 ──
path = 'scripts/daily_pipeline.py'
with open(path) as f:
    c = f.read()
if '"--date", str(target_date)' in c:
    print("✓ daily_pipeline 已修，跳過")
else:
    old = '''            [sys.executable, "twse_ml_eval/ml_scorer.py",
             "--db", "data/db/quant.db", "--mode", "latest", "--score-days", "1"],'''
    new = '''            [sys.executable, "twse_ml_eval/ml_scorer.py",
             "--db", "data/db/quant.db", "--mode", "latest", "--score-days", "1",
             "--date", str(target_date)],'''
    if old in c:
        with open(path, 'w') as f:
            f.write(c.replace(old, new, 1))
        print("✓ daily_pipeline.py ML 步驟已帶 --date")
    else:
        print("❌ daily_pipeline 錨點失敗")

# ── 3. process_pending_backfill.py 帶日期呼叫 ──
path = 'scripts/process_pending_backfill.py'
with open(path) as f:
    c = f.read()
if '"--date", str(d)' in c:
    print("✓ backfill 已修，跳過")
else:
    old = '''    subprocess.run([sys.executable, "twse_ml_eval/ml_scorer.py",
                    "--db", "data/db/quant.db", "--mode", "latest", "--score-days", "1"],
                   capture_output=True, text=True, cwd=str(PROJECT))'''
    new = '''    subprocess.run([sys.executable, "twse_ml_eval/ml_scorer.py",
                    "--db", "data/db/quant.db", "--mode", "latest", "--score-days", "1",
                    "--date", str(d)],
                   capture_output=True, text=True, cwd=str(PROJECT))'''
    if old in c:
        with open(path, 'w') as f:
            f.write(c.replace(old, new, 1))
        print("✓ process_pending_backfill.py ML 步驟已帶 --date")
    else:
        print("❌ backfill 錨點失敗")
