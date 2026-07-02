"""
修正 api_monthly_race 的月報酬與最大回撤算法（v2：用行號定位，不怕空白差異）。
原 bug：用 MIN/MAX(total_equity) 當「期初/期末」，
        實際算成「月內最低點到最高點的反彈幅度」，跟真實月報酬無關。
修正：用「最早日期那筆」當期初、「最晚日期那筆」當期末，才是真正的月報酬。
最大回撤也一併修正為「期間內任一高點到其後最深低點」的正確定義。
idempotent。用法：python3 fix_monthly_race_calc_v2.py
"""
path = 'main.py'
with open(path) as f:
    lines = f.readlines()

# 用內容找錨點（鎖定函式邊界），不用整段比對
start_idx = None
end_idx = None
for i, ln in enumerate(lines):
    if 'def api_monthly_race' in ln:
        start_idx = i
        break
if start_idx is None:
    print("❌ 找不到 def api_monthly_race，請確認 main.py 有這個函式")
    raise SystemExit

# 檢查是否已修過
func_block_check = ''.join(lines[start_idx:start_idx+120])
if 'FIXED_MONTHLY_RACE' in func_block_check:
    print("✓ 已修，跳過")
    raise SystemExit

# 找函式結尾：下一個 "@app." 或 "def " （非縮排）出現的地方
for j in range(start_idx+1, len(lines)):
    if lines[j].startswith('@app.') or (lines[j].startswith('def ') and j > start_idx+1):
        end_idx = j
        break
if end_idx is None:
    print("❌ 找不到函式結尾錨點")
    raise SystemExit

old_block = ''.join(lines[start_idx:end_idx])
print(f"鎖定函式範圍：第 {start_idx+1} 行 ~ 第 {end_idx} 行（共 {end_idx-start_idx} 行）")

new_func = '''def api_monthly_race(start_date: str = None):
    """月度競賽排行 FIXED_MONTHLY_RACE：用期初/期末算真實月報酬，回撤用 peak-to-trough"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    if not start_date:
        today = ddate.today()
        start_date = f"{today.year}-{today.month:02d}-01"
    db = SessionLocal()
    try:
        acct_ids = db.execute(_t("SELECT id, name, initial_cash FROM strategy_accounts WHERE id >= 11")).fetchall()
        results = []

        def _first_last_equity(acct_id, sd):
            first = db.execute(_t(
                "SELECT total_equity FROM equity_curve WHERE account_id=:id AND snap_date>=:sd ORDER BY snap_date ASC LIMIT 1"
            ), {"id": acct_id, "sd": sd}).scalar()
            last_row = db.execute(_t(
                "SELECT total_equity, snap_date FROM equity_curve WHERE account_id=:id AND snap_date>=:sd ORDER BY snap_date DESC LIMIT 1"
            ), {"id": acct_id, "sd": sd}).fetchone()
            days = db.execute(_t(
                "SELECT COUNT(*) FROM equity_curve WHERE account_id=:id AND snap_date>=:sd"
            ), {"id": acct_id, "sd": sd}).scalar() or 0
            return first, (last_row[0] if last_row else None), (last_row[1] if last_row else None), days

        def _true_max_drawdown(acct_id, sd):
            seq = db.execute(_t(
                "SELECT total_equity FROM equity_curve WHERE account_id=:id AND snap_date>=:sd ORDER BY snap_date ASC"
            ), {"id": acct_id, "sd": sd}).fetchall()
            if not seq:
                return 0
            peak = float(seq[0][0] or 0)
            max_dd = 0.0
            for (v,) in seq:
                v = float(v or 0)
                if v > peak:
                    peak = v
                if peak > 0:
                    dd = (v / peak - 1) * 100
                    if dd < max_dd:
                        max_dd = dd
            return round(max_dd, 2)

        bench_first = db.execute(_t(
            "SELECT equity FROM benchmark_daily_equity WHERE snap_date>=:sd AND benchmark_code='0050' ORDER BY snap_date ASC LIMIT 1"
        ), {"sd": start_date}).scalar()
        bench_last = db.execute(_t(
            "SELECT equity FROM benchmark_daily_equity WHERE snap_date>=:sd AND benchmark_code='0050' ORDER BY snap_date DESC LIMIT 1"
        ), {"sd": start_date}).scalar()
        bench_start = float(bench_first or 200000)
        bench_end   = float(bench_last or 200000)
        bench_ret   = round((bench_end/bench_start-1)*100, 2) if bench_start else 0

        for acct_id, name, initial_cash in acct_ids:
            init = float(initial_cash or 200000)
            first_eq, last_eq, latest_date, total_days = _first_last_equity(acct_id, start_date)
            start_eq = float(first_eq) if first_eq else init
            end_eq   = float(last_eq) if last_eq else init
            monthly_ret = round((end_eq/start_eq-1)*100, 2) if start_eq else 0
            alpha = round(monthly_ret - bench_ret, 2)
            win_days = db.execute(_t(
                "SELECT COUNT(*) FROM equity_curve WHERE account_id=:id AND snap_date>=:sd AND daily_return>0"
            ), {"id": acct_id, "sd": start_date}).scalar() or 0
            win_rate = round(win_days/total_days*100, 1) if total_days else 0
            max_dd = _true_max_drawdown(acct_id, start_date)
            trade_cnt = db.execute(_t(
                "SELECT COUNT(*) FROM paper_fills WHERE account_id=:id AND execution_date>=:sd"
            ), {"id": acct_id, "sd": start_date}).scalar() or 0
            results.append({
                "account_id": acct_id,
                "account_name": name,
                "monthly_return": monthly_ret,
                "benchmark_0050_return": bench_ret,
                "alpha_vs_0050": alpha,
                "outperform": alpha > 0,
                "total_equity": end_eq,
                "trading_days": total_days,
                "win_rate": win_rate,
                "max_drawdown": max_dd,
                "trade_count": trade_cnt,
                "latest_date": str(latest_date) if latest_date else None,
            })
        results.sort(key=lambda x: x["total_equity"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1
        return {
            "start_date": start_date,
            "benchmark_return": bench_ret,
            "accounts": results,
        }
    finally:
        db.close()


'''

lines[start_idx:end_idx] = [new_func]
with open(path, 'w') as f:
    f.writelines(lines)
print(f"✓ api_monthly_race 已用行號定位替換完成（原 {end_idx-start_idx} 行 → 新函式）")
