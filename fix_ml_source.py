"""
讓 MLTop5 選股可指定資料來源(生產表 or 實驗表)

目的:用回測引擎比較不同 horizon 的 ML 分數在真實交易條件下的績效
     (簡化評估已證實會系統性高估,不可用於決策)

設計:
  strategy_account_configs 加欄位 ml_source
    NULL / 空 → 讀生產表 ml_score_results(預設,前向帳戶不受影響)
    'lgbm_exp_h60' 等 → 讀 ml_score_experiments 的該版本

用法:python3 fix_ml_source.py
"""
import sqlite3

# ── 1. 加欄位 ──
con = sqlite3.connect('data/db/quant.db')
cols = {r[1] for r in con.execute("PRAGMA table_info(strategy_account_configs)")}
if 'ml_source' not in cols:
    con.execute("ALTER TABLE strategy_account_configs ADD COLUMN ml_source TEXT")
    con.commit()
    print("✓ strategy_account_configs 已加 ml_source 欄位")
else:
    print("✓ ml_source 欄位已存在")
con.close()

# ── 2. patch decision_engine ──
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    src = f.read()

if 'ML_SOURCE_SWITCH' in src:
    print("✓ decision_engine 已修,跳過")
    raise SystemExit

# 2a. col_names 加入 ml_source(讓 cfg 讀得到)
old_cols = '''                     "created_at","updated_at","account_name","initial_cash"]'''
new_cols = '''                     "created_at","updated_at","ml_source","account_name","initial_cash"]'''
if old_cols in src:
    src = src.replace(old_cols, new_cols, 1)
    print("✓ col_names 已加 ml_source")
else:
    print("⚠️ col_names 錨點失敗,ml_source 可能讀不到(需手動確認欄位順序)")

# 2b. MLTop5 查詢分流
old_q = '''    if cfg.get("strategy_name") == "MLTop5":
        rows = db.execute(text("""
            SELECT m.code, sm.name, 'LIQUID_MOMENTUM',
                   m.ml_score, 30.0, 50.0,
                   tdf.rsi14, tdf.distance_ma20, tdf.return_5d,
                   o.close
            FROM ml_score_results m'''

new_q = '''    if cfg.get("strategy_name") == "MLTop5":
        # ML_SOURCE_SWITCH:ml_source 指定時改讀實驗表(回測用),
        # 未指定則走生產表(前向帳戶預設行為不變)
        _src = (cfg.get("ml_source") or "").strip()
        if _src:
            rows = db.execute(text("""
                SELECT m.code, sm.name, 'LIQUID_MOMENTUM',
                       m.ml_score, 30.0, 50.0,
                       tdf.rsi14, tdf.distance_ma20, tdf.return_5d,
                       o.close
                FROM ml_score_experiments m
                LEFT JOIN stock_meta sm ON sm.code=m.code
                LEFT JOIN technical_daily_features tdf
                       ON tdf.code=m.code AND tdf.trade_date=:sd
                LEFT JOIN ohlcv_daily o
                       ON o.code=m.code AND o.trade_date=:sd
                WHERE m.score_date=(
                    SELECT MAX(score_date) FROM ml_score_experiments
                    WHERE score_date<=:sd AND model_version=:mv
                )
                  AND m.model_version=:mv
                  AND m.ml_rank <= :rank_limit
                  AND o.close IS NOT NULL AND o.close >= 10
                ORDER BY m.ml_rank ASC
                LIMIT :rank_limit
            """), {"sd": str(signal_date), "mv": _src,
                   "rank_limit": int(cfg.get("candidate_rank_limit") or 5)}).fetchall()
            return [{
                "code": r[0], "name": r[1] or r[0], "stock_class": r[2],
                "final_score": float(r[3] or 0), "risk_score": float(r[4] or 30),
                "momentum_score": float(r[5] or 50),
                "rsi14": float(r[6] or 50), "distance_ma20": float(r[7] or 0),
                "return_5d": float(r[8] or 0), "close": float(r[9] or 0),
            } for r in rows if r[9]]

        rows = db.execute(text("""
            SELECT m.code, sm.name, 'LIQUID_MOMENTUM',
                   m.ml_score, 30.0, 50.0,
                   tdf.rsi14, tdf.distance_ma20, tdf.return_5d,
                   o.close
            FROM ml_score_results m'''

if old_q in src:
    src = src.replace(old_q, new_q, 1)
    print("✓ MLTop5 查詢已加來源分流")
else:
    print("❌ MLTop5 查詢錨點失敗")

with open(path, 'w') as f:
    f.write(src)

print("\n請執行驗證:")
print("  grep -c 'ML_SOURCE_SWITCH' backend/v5/decision_engine.py")
print("  python3 -m py_compile backend/v5/decision_engine.py")
print("  # 回歸測試(A7 行為應不變):")
print("  python3 -c \"from datetime import date; from backend.v5.decision_engine import generate_strategy_decisions; print(generate_strategy_decisions(date(2026,8,20), account_min=17, account_max=17))\"")
