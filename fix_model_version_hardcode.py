"""
工程債:清除硬編碼的 model_version(第12個bug)

背景:8/1 換版 lgbm_v9_clean → lgbm_v10_rebuilt 時,
     只改了 ml_scorer 與 decision_engine,漏掉兩處:
       - backend/api_extensions.py
       - backend/services/ml_review.py
     導致 ML 檢討自 8/1 起持續失敗(「無 lgbm_v9_clean 選股資料」)。

修法:改為動態取資料庫中最新的 model_version,以後換版不需再改程式碼。
"""
import re

HELPER = '''

def _latest_model_version(default="lgbm_v10_rebuilt"):
    """動態取得目前使用中的模型版本(避免硬編碼,換版時不需改程式)"""
    try:
        from sqlalchemy import text as _t
        from backend.models.database import SessionLocal
        db = SessionLocal()
        try:
            v = db.execute(_t(
                "SELECT model_version FROM ml_score_results "
                "ORDER BY score_date DESC, id DESC LIMIT 1")).scalar()
            return v or default
        finally:
            db.close()
    except Exception:
        return default
'''

for path, var in [("backend/api_extensions.py", "MODEL_VERSION"),
                  ("backend/services/ml_review.py", "ML_MODEL")]:
    with open(path) as f:
        c = f.read()
    if "_latest_model_version" in c:
        print(f"✓ {path} 已修,跳過")
        continue

    old = f'{var} = "lgbm_v9_clean"'
    if old not in c:
        print(f"❌ {path} 找不到 {old}")
        continue

    # 常數改為呼叫函式(延遲取值)
    c = c.replace(old, f'{var} = None   # 由 _latest_model_version() 動態取得', 1)
    c += HELPER
    # 所有使用該常數的地方,改為 (VAR or _latest_model_version())
    c = re.sub(rf'\b{var}\b(?!\s*=)(?! = None)',
               f'({var} or _latest_model_version())', c)
    # 修掉定義行被自己替換到的情況
    c = c.replace(f'({var} or _latest_model_version()) = None',
                  f'{var} = None', 1)

    with open(path, 'w') as f:
        f.write(c)
    print(f"✓ {path} 已改為動態取版本")
