"""
選項1：A7 (MLTop5) 完全純 ML——跳過 RSI 和離均線過濾。
只保留風險控制：現金/持股數/股價>=10/流動性/停損停利/5天換股。
理由：ML 模型訓練時已納入動能與離均線資訊，不該再用人工技術面過濾否定模型判斷。
其他策略 (A1-A6) 維持原過濾不變。
idempotent。用法：python3 fix_a7_pure_ml.py
"""
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    c = f.read()

if 'MLTop5 純 ML' in c:
    print("✓ 已套用純 ML，跳過")
    raise SystemExit

# 把 RSI 過熱判斷的條件加上「非 MLTop5」前提
old = '''                # RSI 過熱
                elif rsi and float(rsi) > cfg["max_rsi14"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"RSI={rsi:.0f} > {cfg['max_rsi14']}"

                # RSI 過低
                elif rsi and float(rsi) < cfg["min_rsi14"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"RSI={rsi:.0f} < {cfg['min_rsi14']}"

                # MA20 距離過遠
                elif abs(float(ma_dist or 0)) > cfg["max_distance_ma20_pct"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"離MA20={ma_dist:.1f}% > {cfg['max_distance_ma20_pct']}%"'''

new = '''                # MLTop5 純 ML：跳過 RSI 和離均線等技術面過濾（模型已納入這些資訊）
                elif cfg.get("strategy_name") == "MLTop5":
                    pass  # 不套用技術面過濾，只留現金/持股數/流動性/停損停利

                # RSI 過熱
                elif rsi and float(rsi) > cfg["max_rsi14"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"RSI={rsi:.0f} > {cfg['max_rsi14']}"

                # RSI 過低
                elif rsi and float(rsi) < cfg["min_rsi14"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"RSI={rsi:.0f} < {cfg['min_rsi14']}"

                # MA20 距離過遠
                elif abs(float(ma_dist or 0)) > cfg["max_distance_ma20_pct"]:
                    action = "SKIP"
                    blocked = True
                    blocked_reason = f"離MA20={ma_dist:.1f}% > {cfg['max_distance_ma20_pct']}%"'''

if old in c:
    c = c.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ A7 (MLTop5) 已設為純 ML：跳過 RSI 和離均線過濾")
    print("  保留：現金/持股數/股價>=10/流動性/停損8%/停利15%/5天換股")
else:
    print("❌ 找不到過濾邏輯，請貼 117-133 行給 Claude")
