"""v5c_competition_stats.py - 月度競賽加入勝率/回撤"""
import subprocess

with open("main.py") as f:
    c = f.read()

# 找 api_monthly_race 函式，加入勝率和回撤計算
old = """        results = []
        for i, r in enumerate(rows):
            init = float(r[2] or 200000)
            start_eq = float(r[3] or init)
            end_eq   = float(r[4] or init)
            monthly_ret = round((end_eq/start_eq-1)*100, 2) if start_eq else 0
            alpha = round(monthly_ret - bench_ret, 2)

            results.append({
                "rank": i+1,
                "account_id": r[0],
                "account_name": r[1],
                "monthly_return": monthly_ret,
                "benchmark_0050_return": bench_ret,
                "alpha_vs_0050": alpha,
                "outperform": alpha > 0,
                "total_equity": end_eq,
                "trading_days": r[5],
                "latest_date": r[7],
            })"""

new = """        results = []
        for i, r in enumerate(rows):
            init = float(r[2] or 200000)
            start_eq = float(r[3] or init)
            end_eq   = float(r[4] or init)
            min_eq   = float(r[6] or end_eq)
            monthly_ret = round((end_eq/start_eq-1)*100, 2) if start_eq else 0
            alpha = round(monthly_ret - bench_ret, 2)
            max_dd = round((min_eq/start_eq-1)*100, 2) if start_eq else 0

            # 勝率：正報酬日 / 總交易日
            win_days = db.execute(_t("""
                SELECT COUNT(*) FROM equity_curve
                WHERE account_id=:id AND snap_date>=:sd AND daily_return > 0
            """), {"id": r[0], "sd": start_date}).scalar() or 0
            total_days = int(r[5] or 1)
            win_rate = round(win_days / total_days * 100, 1) if total_days else 0

            # 交易次數
            trade_cnt = db.execute(_t("""
                SELECT COUNT(*) FROM paper_fills
                WHERE account_id=:id AND execution_date>=:sd
            """), {"id": r[0], "sd": start_date}).scalar() or 0

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
            })"""

if old in c:
    c = c.replace(old, new)
    print("✓ monthly/race 加入勝率/回撤/交易次數")
    with open("main.py","w") as f:
        f.write(c)
else:
    print("❌ 找不到，略過")

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
