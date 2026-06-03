"""
修正 A7 MLTop5 選股查詢，加入 model_version='lgbm_v9_clean' 過濾，
避免誤選到殘留的 v8_rf_v1 分數。idempotent，重複跑沒事。
用法：python3 fix_a7_model_filter.py
"""
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    content = f.read()

if "m.model_version='lgbm_v9_clean'" in content or 'm.model_version="lgbm_v9_clean"' in content:
    print("✓ A7 已過濾 lgbm_v9_clean，跳過")
else:
    old = """            WHERE m.score_date=(
                SELECT MAX(score_date) FROM ml_score_results WHERE score_date<=:sd
            )
              AND m.ml_rank <= 5"""
    new = """            WHERE m.score_date=(
                SELECT MAX(score_date) FROM ml_score_results
                WHERE score_date<=:sd AND model_version='lgbm_v9_clean'
            )
              AND m.model_version='lgbm_v9_clean'
              AND m.ml_rank <= 5"""
    if old in content:
        content = content.replace(old, new)
        with open(path, 'w') as f:
            f.write(content)
        print("✓ A7 MLTop5 已加入 model_version='lgbm_v9_clean' 過濾")
    else:
        print("❌ 找不到目標位置，請貼 MLTop5 區塊給 Claude")
