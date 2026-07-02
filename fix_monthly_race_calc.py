"""
修正 api_monthly_race 的月報酬與最大回撤算法。
原 bug：用 MIN/MAX(total_equity) 當「期初/期末」，
        實際算成「月內最低點到最高點的反彈幅度」，跟真實月報酬無關。
修正：用「最早日期那筆」當期初、「最晚日期那筆」當期末，才是真正的月報酬。
最大回撤也一併修正為「期間內任一高點到其後最深低點」的正確定義。
idempotent。用法：python3 fix_monthly_race_calc.py
"""
path = 'main.py'
with open(path) as f:
    c = f.read()

if 'FIXED_MONTHLY_RACE' in c:
    print("✓ 已修，跳過")
    raise SystemExit

old = '''        # 策略帳戶月報酬
        rows = db.execute(_t("""
            SELECT a.id, a.name, a.initial_cash,
                   MIN(eq.total_equity) as start_eq,
                   MAX(eq.total_equity) as end_eq,
                   COUNT(eq.id) as days,
                   MIN(eq.total_equity) as min_eq,
                   MAX(eq.snap_date) as latest_date
            FROM strategy_accounts a
            LEFT JOIN equity_curve eq ON eq.account_id=a.id
                AND eq.snap_date >= :sd
            WHERE a.id >= 11
            GROUP BY a.id, a.name, a.initial_cash
            ORDER BY end_eq DESC
        """), {"sd": start_date}).fetchall()
        # 0050 benchmark 月報酬
        bench = db.execute(_t("""
            SELECT MIN(equity) as start_eq, MAX(equity) as end_eq
            FROM benchmark_daily_equity
            WHERE snap_date >= :sd AND benchmark_code='0050'
        """), {"sd": start_date}).fetchone()
        bench_start = float(bench[0] or 200000) if bench else 200000
        bench_end   = float(bench[1] or 200000) if bench else 200000
        bench_ret   = round((bench_end/bench_start-1)*100, 2) if bench_start else 0
        results = []
        for i, r in enumerate(rows):
            init = float(r[2] or 200000)
            start_eq = float(r[3] or init)
            end_eq   = float(r[4] or init)
            monthly_ret = round((end_eq/start_eq-1)*100, 2) if start_eq else 0
            alpha = round(monthly_ret - bench_ret, 2)
            # 勝率
            win_days = db.execute(_t(
                "SELECT COUNT(*) FROM equity_curve WHERE account_id=:id AND snap_date>=:sd AND daily_return>0"
            ), {"id": r[0], "sd": start_date}).scalar() or 0
            total_days = int(r[5] or 1)
            win_rate = round(win_days/total_days*100, 1) if total_days else 0
            # 最大回撤
            min_eq = db.execute(_t(
                "SELECT MIN(total_equity) FROM equity_curve WHERE account_id=:id AND snap_date>=:sd"
            ), {"id": r[0], "sd": start_date}).scalar() or end_eq
            max_dd = round((float(min_eq)/start_eq-1)*100, 2) if start_eq else 0
            # 交易次數
            trade_cnt = db.execute(_t(
                "SELECT COUNT(*) FROM paper_fills WHERE account_id=:id AND execution_date>=:sd"
            ), {"id": r[0], "sd": start_date}).scalar() or 0
            results.append({
                "rank": i+1,
                "account_id": r[0],
                "account_name": r[1],
                "monthly_return": monthly_ret,
                "benchmark_0050_return": bench_ret,
                "alpha_vs_0050": alpha,
                "outperform": alpha > 0,
                "total_equity": end_eq,
                "trading_days": total_days,
                "win_rate": win_rate,
                "max_drawdown": max_dd,
                "trade_count": trade_cnt,
                "latest_date": r[7],
            })'''

new = '''        # FIXED_MONTHLY_RACE：用「最早日期那筆」當期初、「最晚日期那筆」當期末
        # （原 bug 用 MIN/MAX(total_equity) 算成「月內最低點到最高點」，不是真實月報酬）
        acct_ids = db.execute(_t("SELECT id, name, initial_cash FROM strategy_accounts WHERE id >= 11")).fetchall()
        results = []

        def _first_last_equity(acct_id, sd):
            first = db.execute(_t(
                "SELECT total_equity FROM equity_curve WHERE account_id=:id AND snap_date>=:sd ORDER BY snap_date ASC LIMIT 1"
            ), {"id": acct_id, "sd": sd}).scalar()
            last = db.execute(_t(
                "SELECT total_equity, snap_date FROM equity_curve WHERE account_id=:id AND snap_date>=:sd ORDER BY snap_date DESC LIMIT 1"
            ), {"id": acct_id, "sd": sd}).fetchone()
            days = db.execute(_t(
                "SELECT COUNT(*) FROM equity_curve WHERE account_id=:id AND snap_date>=:sd"
            ), {"id": acct_id, "sd": sd}).scalar() or 0
            return first, (last[0] if last else None), (last[1] if last else None), days

        def _true_max_drawdown(acct_id, sd):
            """正確定義：期間內任一高點，到其後最深低點的最大跌幅（peak-to-trough）"""
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

        # 0050 benchmark：同樣用期初/期末，不是 MIN/MAX
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
            r["rank"] = i + 1'''

if old in c:
    c = c.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ api_monthly_race 已修正：用期初/期末算月報酬，回撤改為正確的 peak-to-trough 定義")
else:
    print("❌ 找不到目標區塊，請把 main.py 1962-2025 行貼給 Claude")
