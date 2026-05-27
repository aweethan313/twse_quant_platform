"""scripts/v8_ml_scoring.py
V8 機器學習評分系統
用 Random Forest 取代手寫規則，讓高分股真正有預測力
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal

MODEL_VERSION = "v8_rf_v1"


def build_features(db, signal_date: str) -> list:
    """建立特徵矩陣"""
    rows = db.execute(text("""
        SELECT
            ds.code, sm.name,
            ds.momentum_score, ds.chip_score, ds.risk_score,
            ds.valuation_score, ds.core_score, ds.final_score,
            tdf.rsi14, tdf.distance_ma20, tdf.return_5d, tdf.return_1d,
            tdf.ma5, tdf.ma20,
            COALESCE(cd.foreign_net, 0) as foreign_net,
            COALESCE(cd.trust_net, 0) as trust_net,
            COALESCE(cd.dealer_net, 0) as dealer_net,
            mts.position_multiplier,
            mts.risk_level
        FROM daily_scores ds
        LEFT JOIN stock_meta sm ON sm.code=ds.code
        LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code AND tdf.trade_date=:d
        LEFT JOIN chip_daily cd ON cd.code=ds.code AND cd.trade_date=:d
        LEFT JOIN market_timing_signals mts ON mts.trade_date=:d
        WHERE ds.score_date=:d
          AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','NORMAL')
          AND ds.final_action IN ('BUY','WATCH')
        ORDER BY ds.final_score DESC
        LIMIT 200
    """), {"d": signal_date}).fetchall()

    features = []
    for r in rows:
        features.append({
            "code": r[0], "name": r[1],
            "momentum_score": float(r[2] or 50),
            "chip_score": float(r[3] or 50),
            "risk_score": float(r[4] or 50),
            "valuation_score": float(r[5] or 50),
            "core_score": float(r[6] or 50),
            "final_score": float(r[7] or 50),
            "rsi14": float(r[8] or 50),
            "distance_ma20": float(r[9] or 0),
            "return_5d": float(r[10] or 0),
            "return_1d": float(r[11] or 0),
            "foreign_net": float(r[14] or 0),
            "trust_net": float(r[15] or 0),
            "dealer_net": float(r[16] or 0),
            "market_multiplier": float(r[17] or 1.0),
        })
    return features


def train_model(db, start_date="2025-01-01", end_date=None):
    """訓練 ML 模型（Random Forest）"""
    try:
        from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import mean_absolute_error
        import numpy as np
    except ImportError:
        print("⚠️ 需要安裝 scikit-learn: pip install scikit-learn --break-system-packages")
        return None

    if not end_date:
        end_date = str(date.today() - timedelta(days=10))

    print(f"訓練資料：{start_date} ~ {end_date}")

    # 取訓練資料
    rows = db.execute(text("""
        SELECT
            ds.code, ds.score_date,
            ds.momentum_score, ds.chip_score, ds.risk_score,
            ds.valuation_score, ds.core_score, ds.final_score,
            tdf.rsi14, tdf.distance_ma20, tdf.return_5d, tdf.return_1d,
            COALESCE(cd.foreign_net, 0), COALESCE(cd.trust_net, 0),
            COALESCE(mts.position_multiplier, 1.0),
            cfr.return_5d as target
        FROM daily_scores ds
        LEFT JOIN technical_daily_features tdf ON tdf.code=ds.code AND tdf.trade_date=ds.score_date
        LEFT JOIN chip_daily cd ON cd.code=ds.code AND cd.trade_date=ds.score_date
        LEFT JOIN market_timing_signals mts ON mts.trade_date=ds.score_date
        LEFT JOIN candidate_forward_returns cfr ON cfr.code=ds.code AND cfr.signal_date=ds.score_date
        WHERE ds.score_date >= :s AND ds.score_date <= :e
          AND cfr.return_5d IS NOT NULL
          AND ds.stock_class NOT IN ('ETF_INCOME','ILLIQUID_RISK','NORMAL')
        ORDER BY ds.score_date
    """), {"s": start_date, "e": end_date}).fetchall()

    if len(rows) < 50:
        print(f"⚠️ 訓練資料不足（{len(rows)}筆），需要至少50筆")
        return None

    import numpy as np
    X = np.array([[
        float(r[2] or 50), float(r[3] or 50), float(r[4] or 50),
        float(r[5] or 50), float(r[6] or 50), float(r[7] or 50),
        float(r[8] or 50), float(r[9] or 0), float(r[10] or 0),
        float(r[11] or 0), float(r[12] or 0), float(r[13] or 0),
        float(r[14] or 1.0),
    ] for r in rows])
    y = np.array([float(r[15]) for r in rows])

    feature_names = ["momentum","chip","risk","valuation","core","final_score",
                     "rsi14","dist_ma20","return_5d","return_1d",
                     "foreign_net","trust_net","market_mult"]

    # Walk-forward 評估
    tscv = TimeSeriesSplit(n_splits=3)
    scores = []
    for train_idx, val_idx in tscv.split(X):
        rf = RandomForestRegressor(n_estimators=100, max_depth=5, random_state=42)
        rf.fit(X[train_idx], y[train_idx])
        pred = rf.predict(X[val_idx])
        mae = mean_absolute_error(y[val_idx], pred)
        scores.append(mae)
        print(f"  Fold MAE: {mae:.3f}%")

    print(f"  平均 MAE: {np.mean(scores):.3f}%")

    # 全量訓練
    rf_final = RandomForestRegressor(n_estimators=200, max_depth=6, random_state=42)
    rf_final.fit(X, y)

    # 特徵重要性
    importance = dict(zip(feature_names, rf_final.feature_importances_.tolist()))
    importance_sorted = sorted(importance.items(), key=lambda x: -x[1])
    print("\n特徵重要性：")
    for fname, imp in importance_sorted[:8]:
        print(f"  {fname:20} {imp:.4f}")

    # 儲存模型
    import pickle
    model_path = Path("data/models")
    model_path.mkdir(parents=True, exist_ok=True)
    model_file = model_path / f"{MODEL_VERSION}.pkl"
    with open(model_file, "wb") as f:
        pickle.dump({"model": rf_final, "features": feature_names,
                     "importance": importance_sorted, "train_end": end_date}, f)
    print(f"\n✓ 模型儲存：{model_file}")
    return rf_final, feature_names, importance_sorted


def score_today(db, signal_date: str = None):
    """用 ML 模型對今日候選股評分"""
    import pickle, numpy as np
    if not signal_date:
        signal_date = str(date.today())

    model_path = Path(f"data/models/{MODEL_VERSION}.pkl")
    if not model_path.exists():
        print("⚠️ 模型不存在，請先執行 --train")
        return []

    with open(model_path, "rb") as f:
        saved = pickle.load(f)
    model = saved["model"]
    feature_names = saved["features"]

    features = build_features(db, signal_date)
    if not features:
        print(f"[ML] {signal_date} 無候選股資料")
        return []

    X = np.array([[
        f["momentum_score"], f["chip_score"], f["risk_score"],
        f["valuation_score"], f["core_score"], f["final_score"],
        f["rsi14"], f["distance_ma20"], f["return_5d"], f["return_1d"],
        f["foreign_net"], f["trust_net"], f["market_multiplier"],
    ] for f in features])

    preds = model.predict(X)
    results = []
    for i, (feat, pred) in enumerate(zip(features, preds)):
        ml_score = float(np.clip(pred * 10 + 50, 0, 100))
        results.append({**feat, "ml_score": round(ml_score, 2),
                        "predicted_return_5d": round(float(pred), 3),
                        "ml_rank": i + 1})

    results.sort(key=lambda x: -x["ml_score"])
    for i, r in enumerate(results):
        r["ml_rank"] = i + 1

    # 寫入 DB
    for r in results:
        db.execute(text("""
            INSERT INTO ml_score_results
                (score_date, code, stock_name, ml_score, ml_rank,
                 predicted_return_5d, model_version)
            VALUES (:d,:c,:n,:ms,:mr,:pr,:mv)
            ON CONFLICT(score_date, code) DO UPDATE SET
                ml_score=excluded.ml_score, ml_rank=excluded.ml_rank,
                predicted_return_5d=excluded.predicted_return_5d
        """), {"d": signal_date, "c": r["code"], "n": r["name"],
               "ms": r["ml_score"], "mr": r["ml_rank"],
               "pr": r["predicted_return_5d"], "mv": MODEL_VERSION})
    db.commit()

    print(f"[ML] {signal_date} ML評分完成，Top5：")
    for r in results[:5]:
        print(f"  #{r['ml_rank']} {r['code']} {r['name']} ML分={r['ml_score']:.1f} 預測5日={r['predicted_return_5d']:+.2f}%")

    return results


def run():
    import sys
    db = SessionLocal()
    try:
        if "--train" in sys.argv:
            s = sys.argv[sys.argv.index("--train") + 1] if len(sys.argv) > sys.argv.index("--train") + 1 else "2025-01-01"
            result = train_model(db, s)
            if result:
                print("\n✅ 模型訓練完成")
        else:
            d = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else str(date.today())
            score_today(db, d)
    finally:
        db.close()

if __name__ == "__main__":
    run()
