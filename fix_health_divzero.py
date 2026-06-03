"""
修 strategy_health_scores 除零 bug：
  profit_factor = sum(wins)/sum(loss) 當 sum(loss)=0 時除零。
  改成檢查 sum(loss_amt) > 0。
idempotent。用法：python3 fix_health_divzero.py
"""
path = 'scripts/v6_update_strategy_health_scores.py'
with open(path) as f:
    c = f.read()

old = "                profit_factor = sum(wins_amt)/sum(loss_amt) if loss_amt else 99.0"
new = "                _loss_sum = sum(loss_amt)\n                profit_factor = sum(wins_amt)/_loss_sum if _loss_sum > 0 else 99.0"

if "_loss_sum" in c:
    print("✓ 已修，跳過")
elif old in c:
    c = c.replace(old, new)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ 除零 bug 已修（改檢查 sum(loss_amt) > 0）")
else:
    print("❌ 找不到目標行，請貼第 112 行給 Claude")
